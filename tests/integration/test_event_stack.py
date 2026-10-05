"""Opt-in process-level outage test for this repository's local Compose stack."""

import asyncio
import os
import subprocess
from collections.abc import Awaitable, Callable
from uuid import UUID, uuid4

import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import create_async_engine
from voice_platform_contracts.events import EventEnvelope
from voice_platform_db import create_session_factory
from voice_platform_db.models import DomainEvent, Lead


def compose(*args: str) -> None:
    subprocess.run(["docker", "compose", *args], check=True, capture_output=True, timeout=60)


async def eventually(check: Callable[[], Awaitable[bool]]) -> None:
    async with asyncio.timeout(15):
        while not await check():
            await asyncio.sleep(0.1)


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.failure
async def test_compose_redis_outage_and_relay_restart() -> None:
    if os.getenv("RUN_EVENT_STACK_TESTS") != "1":
        pytest.skip("opt in with RUN_EVENT_STACK_TESTS=1 against the local Compose stack")
    engine = create_async_engine(os.environ["STACK_DATABASE_URL"])
    sessions = create_session_factory(engine)
    redis = Redis.from_url(os.environ["STACK_REDIS_URL"], decode_responses=True)
    lead_id: UUID | None = None
    event_id: UUID | None = None
    client = httpx.AsyncClient(base_url=os.environ["STACK_GATEWAY_URL"], timeout=5)
    try:
        await asyncio.to_thread(compose, "stop", "redis")
        response = await client.post(
            "/v1/leads",
            json={"display_name": "Outage Test", "synthetic_profile_key": f"outage-{uuid4()}"},
        )
        assert response.status_code == 201  # Domain commit does not depend on Redis.
        lead_id = UUID(response.json()["id"])

        async def failure_recorded() -> bool:
            nonlocal event_id
            async with sessions() as session:
                row = await session.scalar(
                    select(DomainEvent).where(DomainEvent.aggregate_id == lead_id)
                )
                assert row is not None
                event_id = row.event_id
                return row.published_at is None and row.publish_attempts > 0

        await eventually(failure_recorded)
        await asyncio.to_thread(compose, "stop", "event-relay")
        await asyncio.to_thread(compose, "up", "-d", "--wait", "redis")
        await asyncio.to_thread(compose, "start", "event-relay")

        async def published() -> bool:
            async with sessions() as session:
                row = await session.get(DomainEvent, event_id)
                assert row is not None
                return row.published_at is not None

        await eventually(published)
        events = await redis.xrange("events.domain")
        matching = [
            message_id
            for message_id, fields in events
            if EventEnvelope.model_validate_json(fields["body"]).event_id == event_id
        ]
        assert matching
        await redis.xdel("events.domain", *matching)
        assert (await client.get(f"/v1/leads/{lead_id}")).status_code == 200
    finally:
        await asyncio.to_thread(compose, "up", "-d", "--wait", "redis")
        await asyncio.to_thread(compose, "start", "event-relay")
        await client.aclose()
        await redis.aclose()
        async with sessions.begin() as session:
            if event_id is not None:
                await session.execute(delete(DomainEvent).where(DomainEvent.event_id == event_id))
            if lead_id is not None:
                await session.execute(delete(Lead).where(Lead.id == lead_id))
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.failure
async def test_compose_database_and_lead_outages_recover_without_bad_mutations() -> None:
    if os.getenv("RUN_EVENT_STACK_TESTS") != "1":
        pytest.skip("opt in with RUN_EVENT_STACK_TESTS=1 against the local Compose stack")
    engine = create_async_engine(os.environ["STACK_DATABASE_URL"])
    sessions = create_session_factory(engine)
    redis = Redis.from_url(os.environ["STACK_REDIS_URL"], decode_responses=True)
    lead_id: UUID | None = None
    async with httpx.AsyncClient(base_url=os.environ["STACK_GATEWAY_URL"], timeout=8) as client:

        async def healthy() -> bool:
            return (await client.get("/health")).status_code == 200

        try:
            response = await client.post(
                "/v1/leads",
                json={
                    "display_name": "Restart Audit",
                    "synthetic_profile_key": f"restart-{uuid4()}",
                },
            )
            assert response.status_code == 201
            lead_id = UUID(response.json()["id"])
            path = f"/v1/leads/{lead_id}"
            await asyncio.to_thread(compose, "stop", "postgres")
            for method, endpoint, body in [
                ("GET", "/health", None),
                ("GET", "/v1/leads", None),
                ("PATCH", path, {"status": "CONTACTED", "expected_version": 1}),
            ]:
                response = await client.request(method, endpoint, json=body)
                assert response.status_code == 503, response.text
                assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]
            operations = await client.get("/v1/observability")
            assert operations.status_code == 200
            assert any(m["errors"] > 0 for m in operations.json()["lead"]["metrics"])
            await asyncio.to_thread(compose, "up", "-d", "--wait", "postgres")
            await eventually(healthy)
            assert (await client.get(path)).json()["version"] == 1
            await asyncio.to_thread(compose, "stop", "lead-service")
            assert (await client.get("/v1/leads")).status_code == 503
            await asyncio.to_thread(compose, "start", "lead-service")
            await eventually(healthy)
            updated = await client.patch(path, json={"status": "CONTACTED", "expected_version": 1})
            assert updated.status_code == 200, updated.text
            assert updated.json()["display_name"] == "Restart Audit"
            assert updated.json()["version"] == 2

            async def published() -> bool:
                async with sessions() as session:
                    rows = (
                        await session.scalars(
                            select(DomainEvent).where(DomainEvent.aggregate_id == lead_id)
                        )
                    ).all()
                    return len(rows) == 2 and all(row.published_at is not None for row in rows)

            await eventually(published)
        finally:
            await asyncio.to_thread(compose, "up", "-d", "--wait", "postgres")
            await asyncio.to_thread(compose, "start", "lead-service")
            if lead_id is not None:
                async with sessions.begin() as session:
                    await session.execute(
                        delete(DomainEvent).where(DomainEvent.aggregate_id == lead_id)
                    )
                    await session.execute(delete(Lead).where(Lead.id == lead_id))
                entries = await redis.xrange("events.domain")
                ids = [
                    mid
                    for mid, fields in entries
                    if EventEnvelope.model_validate_json(fields["body"]).aggregate_id == lead_id
                ]
                if ids:
                    await redis.xdel("events.domain", *ids)
            await redis.aclose()
            await engine.dispose()


@pytest.mark.integration
@pytest.mark.failure
@pytest.mark.parametrize(
    "service,module", [("api-gateway", "api_gateway.app"), ("lead-service", "lead_service.app")]
)
def test_compose_images_reject_demo_tokens_in_production(service: str, module: str) -> None:
    if os.getenv("RUN_EVENT_STACK_TESTS") != "1":
        pytest.skip("opt in with RUN_EVENT_STACK_TESTS=1 against the local Compose stack")
    result = subprocess.run(
        [
            "docker",
            "compose",
            "run",
            "--rm",
            "--no-deps",
            "-e",
            "APP_ENV=production",
            "-e",
            "LEAD_SERVICE_AUTH_TOKEN=local-lead-service-token",
            service,
            "python",
            "-c",
            f"import {module}",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode != 0
    assert "LEAD_SERVICE_AUTH_TOKEN must be a non-demo secret" in result.stderr


@pytest.mark.integration
def test_container_receives_special_character_database_password_unchanged() -> None:
    if os.getenv("RUN_EVENT_STACK_TESTS") != "1":
        pytest.skip("opt in with RUN_EVENT_STACK_TESTS=1 against the local Compose stack")
    password = " spaces/@:$+% secret "
    code = (
        "from voice_platform_config.settings import database_url_from_env; "
        "from sqlalchemy.engine import make_url; "
        f"assert make_url(database_url_from_env()).password == {password!r}"
    )
    result = subprocess.run(
        [
            "docker",
            "compose",
            "run",
            "--rm",
            "--no-deps",
            "event-relay",
            "python",
            "-c",
            code,
        ],
        env=os.environ | {"POSTGRES_PASSWORD": password},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr

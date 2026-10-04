"""Exercise both real ASGI applications and PostgreSQL, including COMMIT failures."""

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from api_gateway.app import create_app as gateway_app
from api_gateway.client import LeadServiceClient
from api_gateway.settings import GatewaySettings
from lead_service.app import create_app as lead_app
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from voice_platform_db import create_async_engine, create_session_factory
from voice_platform_db.models import DomainEvent, Lead, QualificationAnswer, QualificationProfile

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@dataclass
class System:
    client: httpx.AsyncClient
    sessions: async_sessionmaker[AsyncSession]
    key: str


@pytest_asyncio.fixture
async def system() -> AsyncIterator[System]:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL required")
    engine = create_async_engine(url)
    sessions = create_session_factory(engine)
    key = f"gateway-audit-{uuid4().hex}"
    lead = lead_app(session_factory=sessions, app_env="test", service_auth_token="audit-token")
    try:
        async with (
            lead.router.lifespan_context(lead),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(lead), base_url="http://lead"
            ) as downstream,
        ):
            gateway = gateway_app(
                settings=GatewaySettings("test", "http://lead", "audit-token", 5),
                client=LeadServiceClient(downstream, service_auth_token="audit-token"),
            )
            async with (
                gateway.router.lifespan_context(gateway),
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(gateway), base_url="http://gateway"
                ) as client,
            ):
                yield System(client, sessions, key)
    finally:
        async with sessions.begin() as session:
            ids = select(Lead.id).where(Lead.synthetic_profile_key == key)
            await session.execute(delete(DomainEvent).where(DomainEvent.aggregate_id.in_(ids)))
            await session.execute(delete(Lead).where(Lead.synthetic_profile_key == key))
        await engine.dispose()


async def test_gateway_lead_lifecycle_preserves_patch_and_outbox_context(system: System) -> None:
    payload = {
        "display_name": "Gateway Audit Lead",
        "synthetic_profile_key": system.key,
        "intent": "Work visa",
        "initial_answers": {"experience": 4},
    }
    created = await system.client.post(
        "/v1/leads", json=payload, headers={"X-Request-ID": system.key}
    )
    assert created.status_code == 201, created.text
    lead = created.json()
    path = f"/v1/leads/{lead['id']}"
    updated = await system.client.patch(
        path,
        json={"status": "CONTACTED", "expected_version": 1},
        headers={"X-Request-ID": system.key},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["display_name"] == payload["display_name"]
    assert updated.json()["intent"] == "Work visa"
    cleared = await system.client.patch(
        path, json={"intent": None, "expected_version": 2}, headers={"X-Request-ID": system.key}
    )
    assert cleared.status_code == 200
    assert cleared.json()["intent"] is None
    assert cleared.json()["version"] == 3
    assert (await system.client.post("/v1/leads", json=payload)).status_code == 409
    assert (
        await system.client.patch(path, json={"status": "NEW", "expected_version": 1})
    ).status_code == 409
    assert (
        await system.client.patch(
            f"/v1/leads/{uuid4()}", json={"status": "NEW", "expected_version": 1}
        )
    ).status_code == 404
    qualification = (await system.client.get(f"{path}/qualification")).json()
    assert qualification["answers"][0]["answer_status"] == "PROVISIONAL"
    async with system.sessions() as session:
        events = (
            await session.scalars(
                select(DomainEvent)
                .where(DomainEvent.request_id == system.key)
                .order_by(DomainEvent.aggregate_version)
            )
        ).all()
        assert [e.event_type for e in events] == ["lead.created", "lead.updated", "lead.updated"]
        assert [e.aggregate_version for e in events] == [1, 2, 3]


@pytest.mark.parametrize(
    "params",
    [
        {"status": "bad\x00status"},
        {"status": "x" * 33},
        {"offset": str(2**64)},
    ],
)
async def test_invalid_query_values_are_rejected_before_sql(
    system: System,
    params: dict[str, str],
) -> None:
    response = await system.client.get("/v1/leads", params=params)
    assert response.status_code == 422
    assert response.json()["error"]["message"] == "invalid request"


@pytest.mark.failure
async def test_deferred_commit_failure_is_not_reported_as_success(system: System) -> None:
    # This constraint executes at COMMIT, after ORM flush and response serialization.
    # It is scoped to this request and removed even when the assertion fails.
    trigger = f"audit_commit_{uuid4().hex}"
    async with system.sessions.begin() as session:
        await session.execute(
            text(f"""
            CREATE FUNCTION lead.{trigger}() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'private deferred commit failure' USING ERRCODE='08006';
            END $$
        """)
        )
        await session.execute(
            text(f"""
            CREATE CONSTRAINT TRIGGER {trigger} AFTER INSERT ON lead.leads
            DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
            WHEN (NEW.synthetic_profile_key = '{system.key}') EXECUTE FUNCTION lead.{trigger}()
        """)
        )
    try:
        models = (Lead, QualificationProfile, QualificationAnswer, DomainEvent)
        async with system.sessions() as session:
            before = [await session.scalar(select(func.count()).select_from(m)) for m in models]
        response = await system.client.post(
            "/v1/leads",
            headers={"X-Request-ID": system.key},
            json={
                "display_name": "Rejected commit",
                "synthetic_profile_key": system.key,
                "initial_answers": {"experience": 4},
            },
        )
        assert response.status_code == 503, response.text
        assert "private" not in response.text
        assert response.headers["X-Request-ID"] == system.key
        async with system.sessions() as session:
            after = [await session.scalar(select(func.count()).select_from(m)) for m in models]
            assert after == before  # Lead, profile, answers, and outbox all rolled back.
    finally:
        async with system.sessions.begin() as session:
            await session.execute(text(f"DROP TRIGGER {trigger} ON lead.leads"))
            await session.execute(text(f"DROP FUNCTION lead.{trigger}()"))

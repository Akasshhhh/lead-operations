"""Opt-in full Compose Gateway → Conversation → Lead live-turn verification."""

from __future__ import annotations

import asyncio
import os
import subprocess
from collections.abc import Awaitable, Callable
from uuid import UUID, uuid4

import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import create_async_engine
from voice_platform_contracts.events import EventEnvelope
from voice_platform_db import create_session_factory
from voice_platform_db.models import (
    Conversation,
    DomainEvent,
    Lead,
    LeadScoreHistory,
    Message,
    QualificationProfile,
)


def compose(*args: str) -> None:
    subprocess.run(["docker", "compose", *args], check=True, capture_output=True, timeout=60)


async def eventually(check: Callable[[], Awaitable[bool]]) -> None:
    async with asyncio.timeout(20):
        while not await check():
            await asyncio.sleep(0.1)


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.failure
async def test_compose_gateway_conversation_live_turn() -> None:
    if os.getenv("RUN_EVENT_STACK_TESTS") != "1":
        pytest.skip("opt in with RUN_EVENT_STACK_TESTS=1 against the local Compose stack")
    gateway_url = os.environ["STACK_GATEWAY_URL"]
    database_url = os.environ["STACK_DATABASE_URL"]
    redis_url = os.environ["STACK_REDIS_URL"]
    engine = create_async_engine(database_url)
    sessions = create_session_factory(engine)
    redis = Redis.from_url(redis_url, decode_responses=True)
    lead_id: UUID | None = None
    conversation_id: UUID | None = None
    call_id: UUID | None = None
    try:
        async with httpx.AsyncClient(base_url=gateway_url, timeout=8) as client:
            lead = await client.post(
                "/v1/leads",
                json={
                    "display_name": "Compose Conversation Lead",
                    "synthetic_profile_key": f"compose-conversation-{uuid4().hex}",
                },
            )
            assert lead.status_code == 201, lead.text
            lead_id = UUID(lead.json()["id"])
            created = await client.post("/v1/conversations", json={"lead_id": str(lead_id)})
            assert created.status_code == 201, created.text
            conversation_id = UUID(created.json()["id"])
            call = await client.post(f"/v1/conversations/{conversation_id}/calls", json={})
            assert call.status_code == 201, call.text
            call_id = UUID(call.json()["id"])
            current = await client.post(
                f"/v1/conversations/{conversation_id}/transitions",
                json={"target_state": "CONNECTING", "expected_version": 1},
            )
            assert current.status_code == 200
            current = await client.post(
                f"/v1/conversations/{conversation_id}/calls/{call_id}/transitions",
                json={"target_status": "CONNECTING", "expected_version": 1},
            )
            assert current.status_code == 200
            current = await client.post(
                f"/v1/conversations/{conversation_id}/calls/{call_id}/transitions",
                json={"target_status": "CONNECTED", "expected_version": 2},
            )
            assert current.status_code == 200
            current = await client.post(
                f"/v1/conversations/{conversation_id}/transitions",
                json={"target_state": "GREETING", "expected_version": 2},
            )
            assert current.status_code == 200
            first_turn = {
                "turn_id": str(uuid4()),
                "call_id": str(call_id),
                "expected_version": current.json()["version"],
                "user_text": "I have a masters degree.",
                "facts": [
                    {"field_key": "education_level", "value": "masters", "status": "CONFIRMED"}
                ],
            }
            turn = await client.post(f"/v1/conversations/{conversation_id}/turns", json=first_turn)
            assert turn.status_code == 200, turn.text
            assert turn.json()["qualification"]["score"]["score"] == 10
            live = await client.get(f"/v1/conversations/{conversation_id}/live-state")
            assert live.status_code == 200, live.text
            assert live.json()["qualification"]["score"]["rule_version"] == "baseline-v1"
            history = await client.get(
                f"/v1/conversations/{conversation_id}/history", params={"query": "masters"}
            )
            assert history.status_code == 200, history.text
            assert history.json()["items"][0]["sequence_number"] == 1
            transcript = await client.get(
                f"/v1/conversations/{conversation_id}/transcript", params={"speaker": "USER"}
            )
            assert transcript.status_code == 200, transcript.text
            assert len(transcript.json()["items"]) == 1

            pending_turn = first_turn | {
                "turn_id": str(uuid4()),
                "expected_version": turn.json()["conversation"]["version"],
                "user_text": "I speak advanced English.",
                "facts": [
                    {"field_key": "english_level", "value": "advanced", "status": "CONFIRMED"}
                ],
            }
            try:
                await asyncio.to_thread(compose, "stop", "lead-service")
                for submitted in (pending_turn, first_turn):
                    unavailable = await client.post(
                        f"/v1/conversations/{conversation_id}/turns", json=submitted
                    )
                    assert unavailable.status_code == 503, unavailable.text
                    assert "score" not in unavailable.json()
                    assert "qualification" not in unavailable.json()
                live = await client.get(f"/v1/conversations/{conversation_id}/live-state")
                assert live.status_code == 503
                history = await client.get(f"/v1/conversations/{conversation_id}/history")
                assert history.status_code == 200
                assert [m["turn_status"] for m in history.json()["items"]] == ["APPLIED", "PENDING"]
                current = await client.get(f"/v1/conversations/{conversation_id}")
                assert current.json()["state"] == "DISCOVERY" and current.json()["version"] == 4
            finally:
                await asyncio.to_thread(compose, "start", "lead-service")

                async def healthy() -> bool:
                    return (await client.get("/health")).status_code == 200

                await eventually(healthy)

            for _ in range(2):
                recovered = await client.post(
                    f"/v1/conversations/{conversation_id}/turns", json=pending_turn
                )
                assert recovered.status_code == 200, recovered.text
                assert recovered.json()["qualification"]["score"]["score"] == 20
                assert recovered.json()["call"]["status"] == "CONNECTED"
                assert recovered.json()["conversation"]["version"] == 5
            async with sessions() as session:
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(LeadScoreHistory)
                        .where(LeadScoreHistory.lead_id == lead_id)
                    )
                    == 2
                )

            async def published() -> bool:
                async with sessions() as session:
                    return bool(
                        await session.scalar(
                            select(DomainEvent.event_id).where(
                                DomainEvent.aggregate_id == conversation_id,
                                DomainEvent.published_at.is_not(None),
                            )
                        )
                    )

            await eventually(published)
    finally:
        aggregate_ids: set[UUID] = {
            value for value in (lead_id, conversation_id, call_id) if value is not None
        }
        async with sessions.begin() as session:
            if conversation_id is not None:
                message_ids = list(
                    await session.scalars(
                        select(Message.id).where(Message.conversation_id == conversation_id)
                    )
                )
                aggregate_ids.update(message_ids)
                await session.execute(
                    delete(DomainEvent).where(DomainEvent.aggregate_id == conversation_id)
                )
                await session.execute(
                    delete(DomainEvent).where(DomainEvent.aggregate_id == call_id)
                )
                await session.execute(
                    delete(DomainEvent).where(DomainEvent.aggregate_id.in_(message_ids))
                )
                await session.execute(
                    delete(Conversation).where(Conversation.id == conversation_id)
                )
            if lead_id is not None:
                profile_ids = list(
                    await session.scalars(
                        select(QualificationProfile.id).where(
                            QualificationProfile.lead_id == lead_id
                        )
                    )
                )
                aggregate_ids.update(profile_ids)
                await session.execute(
                    delete(DomainEvent).where(DomainEvent.aggregate_id.in_(profile_ids))
                )
                await session.execute(
                    delete(DomainEvent).where(DomainEvent.aggregate_id == lead_id)
                )
                await session.execute(delete(Lead).where(Lead.id == lead_id))
        entries = await redis.xrange("events.domain")
        if aggregate_ids:
            remove: list[str] = []
            for stream_id, fields in entries:
                envelope = EventEnvelope.model_validate_json(fields["body"])
                if envelope.aggregate_id in aggregate_ids:
                    remove.append(stream_id)
            if remove:
                await redis.xdel("events.domain", *remove)
        await redis.aclose()
        await engine.dispose()

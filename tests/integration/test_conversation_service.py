"""Conversation Service integration against real PostgreSQL and the Lead Service boundary."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any, cast
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from conversation_service.app import create_app as create_conversation_app
from conversation_service.client import (
    LeadServiceClient as ConversationLeadClient,
)
from conversation_service.client import (
    LeadServiceUnavailableError,
)
from conversation_service.service import ConversationService
from lead_service.app import create_app as create_lead_app
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.exc import TimeoutError as DatabaseTimeoutError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from voice_platform_db import create_async_engine, create_session_factory
from voice_platform_db.models import (
    Call,
    Conversation,
    DomainEvent,
    Lead,
    LeadScore,
    LeadScoreHistory,
    Message,
    QualificationProfile,
    TranscriptSegment,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@dataclass
class System:
    lead: httpx.AsyncClient
    conversation: httpx.AsyncClient
    sessions: async_sessionmaker[AsyncSession]
    lead_id: UUID
    conversation_id: UUID
    call_id: UUID
    conversation_app: Any


@pytest_asyncio.fixture
async def system() -> AsyncIterator[System]:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is required")
    engine = create_async_engine(database_url)
    sessions = create_session_factory(engine)
    lead_app = create_lead_app(
        session_factory=sessions, app_env="test", service_auth_token="test-token"
    )
    lead_id: UUID | None = None
    conversation_id: UUID | None = None
    call_id: UUID | None = None
    try:
        async with lead_app.router.lifespan_context(lead_app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(lead_app), base_url="http://lead"
            ) as lead_client:
                create_lead = await lead_client.post(
                    "/v1/leads",
                    headers={"X-Service-Token": "test-token"},
                    json={
                        "display_name": "Conversation Integration Lead",
                        "synthetic_profile_key": f"conversation-integration-{uuid4().hex}",
                    },
                )
                assert create_lead.status_code == 201, create_lead.text
                lead_id = UUID(create_lead.json()["id"])
                lead_transport = httpx.AsyncClient(
                    transport=httpx.ASGITransport(lead_app), base_url="http://lead"
                )
                lead_boundary = ConversationLeadClient(
                    lead_transport, service_auth_token="test-token", timeout_seconds=2
                )
                conversation_app = create_conversation_app(
                    session_factory=sessions,
                    lead_client=lead_boundary,
                    app_env="test",
                    service_auth_token="test-token",
                )
                async with conversation_app.router.lifespan_context(conversation_app):
                    async with httpx.AsyncClient(
                        transport=httpx.ASGITransport(conversation_app),
                        base_url="http://conversation",
                    ) as conversation_client:
                        created = await conversation_client.post(
                            "/v1/conversations",
                            headers={"X-Service-Token": "test-token"},
                            json={"lead_id": str(lead_id)},
                        )
                        assert created.status_code == 201, created.text
                        conversation_id = UUID(created.json()["id"])
                        call = await conversation_client.post(
                            f"/v1/conversations/{conversation_id}/calls",
                            headers={"X-Service-Token": "test-token"},
                            json={"runtime_instance_id": "runtime-test"},
                        )
                        assert call.status_code == 201, call.text
                        call_id = UUID(call.json()["id"])
                        yield System(
                            lead_client,
                            conversation_client,
                            sessions,
                            lead_id,
                            conversation_id,
                            call_id,
                            conversation_app,
                        )
                await lead_transport.aclose()
    finally:
        async with sessions.begin() as session:
            if lead_id is not None:
                conversations = select(Conversation.id).where(Conversation.lead_id == lead_id)
                profiles = select(QualificationProfile.id).where(
                    QualificationProfile.lead_id == lead_id
                )
                await session.execute(
                    delete(DomainEvent).where(
                        (DomainEvent.aggregate_id == lead_id)
                        | DomainEvent.aggregate_id.in_(profiles)
                        | DomainEvent.aggregate_id.in_(conversations)
                        | DomainEvent.aggregate_id.in_(
                            select(Call.id).where(Call.conversation_id.in_(conversations))
                        )
                        | DomainEvent.aggregate_id.in_(
                            select(Message.id).where(Message.conversation_id.in_(conversations))
                        )
                    )
                )
                await session.execute(delete(Conversation).where(Conversation.lead_id == lead_id))
                await session.execute(delete(Lead).where(Lead.id == lead_id))
        await engine.dispose()


async def transition_to_greeting(system: System) -> dict[str, Any]:
    headers = {"X-Service-Token": "test-token", "X-Request-ID": "conversation-test"}
    conversation = await system.conversation.post(
        f"/v1/conversations/{system.conversation_id}/transitions",
        headers=headers,
        json={"target_state": "CONNECTING", "expected_version": 1},
    )
    assert conversation.status_code == 200, conversation.text
    call = await system.conversation.post(
        f"/v1/conversations/{system.conversation_id}/calls/{system.call_id}/transitions",
        headers=headers,
        json={"target_status": "CONNECTING", "expected_version": 1},
    )
    assert call.status_code == 200, call.text
    call = await system.conversation.post(
        f"/v1/conversations/{system.conversation_id}/calls/{system.call_id}/transitions",
        headers=headers,
        json={"target_status": "CONNECTED", "expected_version": 2},
    )
    assert call.status_code == 200, call.text
    conversation = await system.conversation.post(
        f"/v1/conversations/{system.conversation_id}/transitions",
        headers=headers,
        json={"target_state": "GREETING", "expected_version": 2},
    )
    assert conversation.status_code == 200, conversation.text
    return cast(dict[str, Any], conversation.json())


async def test_live_turns_score_in_lead_service_and_advance_conversation(system: System) -> None:
    conversation = await transition_to_greeting(system)
    headers = {"X-Service-Token": "test-token", "X-Request-ID": "turn-request"}
    first_turn = {
        "turn_id": str(uuid4()),
        "call_id": str(system.call_id),
        "expected_version": conversation["version"],
        "user_text": "I have a masters degree.",
        "facts": [{"field_key": "education_level", "value": "masters", "status": "CONFIRMED"}],
    }
    response = await system.conversation.post(
        f"/v1/conversations/{system.conversation_id}/turns", headers=headers, json=first_turn
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["conversation"]["state"] == "DISCOVERY"
    assert body["qualification"]["score"]["score"] == 10
    assert body["qualification"]["score"]["rule_version"] == "baseline-v1"
    assert body["next_action"] == "continue_discovery"

    facts = [
        {"field_key": "years_experience", "value": 0, "status": "CONFIRMED"},
        {"field_key": "english_level", "value": "advanced", "status": "CONFIRMED"},
        {"field_key": "has_job_offer", "value": False, "status": "CONFIRMED"},
        {"field_key": "budget_ready", "value": True, "status": "CONFIRMED"},
        {"field_key": "urgency", "value": "medium", "status": "CONFIRMED"},
    ]
    second_turn = {
        "turn_id": str(uuid4()),
        "call_id": str(system.call_id),
        "expected_version": body["conversation"]["version"],
        "user_text": "Here are the remaining details.",
        "facts": facts,
    }
    response = await system.conversation.post(
        f"/v1/conversations/{system.conversation_id}/turns", headers=headers, json=second_turn
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["conversation"]["state"] == "QUALIFICATION"
    assert body["qualification"]["status"] == "COMPLETE"
    assert body["qualification"]["completeness"] == 100
    assert body["qualification"]["score"]["score"] == 60

    third_turn = {
        "turn_id": str(uuid4()),
        "call_id": str(system.call_id),
        "expected_version": body["conversation"]["version"],
        "user_text": "That is everything.",
    }
    response = await system.conversation.post(
        f"/v1/conversations/{system.conversation_id}/turns", headers=headers, json=third_turn
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["conversation"]["state"] == "DECISION"
    assert body["next_action"] == "determine_next_action"

    completed = await system.conversation.post(
        f"/v1/conversations/{system.conversation_id}/transitions",
        headers=headers,
        json={"target_state": "COMPLETED", "expected_version": body["conversation"]["version"]},
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["state"] == "COMPLETED"

    retry = await system.conversation.post(
        f"/v1/conversations/{system.conversation_id}/turns", headers=headers, json=first_turn
    )
    assert retry.status_code == 200, retry.text
    assert retry.json()["turn_status"] == "APPLIED"
    async with system.sessions() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.conversation_id == system.conversation_id)
            )
            == 3
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(TranscriptSegment)
                .where(TranscriptSegment.conversation_id == system.conversation_id)
            )
            == 3
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(LeadScoreHistory)
                .where(LeadScoreHistory.lead_id == system.lead_id)
            )
            == 2
        )
        score = await session.get(LeadScore, system.lead_id)
        assert score is not None and score.score == 60


async def test_active_session_and_conversation_invariants_are_enforced(system: System) -> None:
    conversation = await transition_to_greeting(system)
    duplicate_call = await system.conversation.post(
        f"/v1/conversations/{system.conversation_id}/calls",
        headers={"X-Service-Token": "test-token"},
        json={},
    )
    assert duplicate_call.status_code == 409
    duplicate_conversation = await system.conversation.post(
        "/v1/conversations",
        headers={"X-Service-Token": "test-token"},
        json={"lead_id": str(system.lead_id)},
    )
    assert duplicate_conversation.status_code == 409
    stale = await system.conversation.post(
        f"/v1/conversations/{system.conversation_id}/transitions",
        headers={"X-Service-Token": "test-token"},
        json={
            "target_state": "DISCOVERY",
            "expected_version": cast(int, conversation["version"]) - 1,
        },
    )
    assert stale.status_code == 409


async def test_contradictory_facts_are_explicit_and_do_not_score(system: System) -> None:
    conversation = await transition_to_greeting(system)
    headers = {"X-Service-Token": "test-token"}

    async def turn(version: int, text: str, value: str, *, resolve: bool = False) -> dict[str, Any]:
        response = await system.conversation.post(
            f"/v1/conversations/{system.conversation_id}/turns",
            headers=headers,
            json={
                "turn_id": str(uuid4()),
                "call_id": str(system.call_id),
                "expected_version": version,
                "user_text": text,
                "facts": [
                    {
                        "field_key": "education_level",
                        "value": value,
                        "status": "CONFIRMED",
                        "resolve_conflict": resolve,
                    }
                ],
            },
        )
        assert response.status_code == 200, response.text
        return cast(dict[str, Any], response.json())

    first = await turn(cast(int, conversation["version"]), "I have a masters.", "masters")
    assert first["qualification"]["score"]["score"] == 10
    second = await turn(
        cast(int, first["conversation"]["version"]), "Actually bachelors.", "bachelors"
    )
    qualification = second["qualification"]
    assert qualification["status"] == "CONTRADICTORY"
    assert qualification["score"]["score"] == 0
    answer = qualification["answers"][0]
    assert answer["answer_status"] == "CONTRADICTORY"
    assert answer["conflict_value"] == "bachelors"
    third = await turn(
        cast(int, second["conversation"]["version"]),
        "I confirm bachelors.",
        "bachelors",
        resolve=True,
    )
    assert third["qualification"]["status"] == "INCOMPLETE"
    assert third["qualification"]["score"]["score"] == 10


async def test_lead_outage_leaves_turn_pending_and_same_turn_retries(
    system: System, monkeypatch: pytest.MonkeyPatch
) -> None:
    conversation = await transition_to_greeting(system)
    turn = {
        "turn_id": str(uuid4()),
        "call_id": str(system.call_id),
        "expected_version": conversation["version"],
        "user_text": "I need help understanding my options.",
        "facts": [{"field_key": "education_level", "value": "masters", "status": "CONFIRMED"}],
    }
    original = system.conversation_app.state.lead_client.get_qualification

    async def unavailable(*_: object, **__: object) -> None:
        raise LeadServiceUnavailableError("simulated lead outage")

    monkeypatch.setattr(system.conversation_app.state.lead_client, "get_qualification", unavailable)
    failed = await system.conversation.post(
        f"/v1/conversations/{system.conversation_id}/turns",
        headers={"X-Service-Token": "test-token"},
        json=turn,
    )
    assert failed.status_code == 503
    monkeypatch.setattr(system.conversation_app.state.lead_client, "get_qualification", original)
    retried = await system.conversation.post(
        f"/v1/conversations/{system.conversation_id}/turns",
        headers={"X-Service-Token": "test-token"},
        json=turn,
    )
    assert retried.status_code == 200, retried.text
    assert retried.json()["turn_status"] == "APPLIED"
    async with system.sessions() as session:
        message = await session.get(Message, UUID(turn["turn_id"]))
        assert message is not None and message.turn_status == "APPLIED"
        assert (
            await session.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.conversation_id == system.conversation_id)
            )
            == 1
        )


async def test_history_pagination_speaker_filter_and_search(system: System) -> None:
    conversation = await transition_to_greeting(system)
    headers = {"X-Service-Token": "test-token"}
    first = await system.conversation.post(
        f"/v1/conversations/{system.conversation_id}/turns",
        headers=headers,
        json={
            "turn_id": str(uuid4()),
            "call_id": str(system.call_id),
            "expected_version": conversation["version"],
            "user_text": "I need a Canada work permit.",
        },
    )
    assert first.status_code == 200, first.text
    second = await system.conversation.post(
        f"/v1/conversations/{system.conversation_id}/turns",
        headers=headers,
        json={
            "turn_id": str(uuid4()),
            "call_id": str(system.call_id),
            "expected_version": first.json()["conversation"]["version"],
            "user_text": "My budget is ready.",
        },
    )
    assert second.status_code == 200, second.text

    latest = await system.conversation.get(
        f"/v1/conversations/{system.conversation_id}/history",
        headers=headers,
        params={"limit": 1},
    )
    assert latest.status_code == 200
    assert [item["sequence_number"] for item in latest.json()["items"]] == [2]
    assert latest.json()["has_more"] is True
    assert latest.json()["next_before_sequence"] == 2

    older = await system.conversation.get(
        f"/v1/conversations/{system.conversation_id}/history",
        headers=headers,
        params={"limit": 1, "before_sequence": 2},
    )
    assert [item["sequence_number"] for item in older.json()["items"]] == [1]
    newer = await system.conversation.get(
        f"/v1/conversations/{system.conversation_id}/history",
        headers=headers,
        params={"limit": 1, "after_sequence": 1},
    )
    assert [item["sequence_number"] for item in newer.json()["items"]] == [2]
    searched = await system.conversation.get(
        f"/v1/conversations/{system.conversation_id}/transcript",
        headers=headers,
        params={"query": "CANADA", "speaker": "USER"},
    )
    assert searched.status_code == 200
    assert [item["sequence_number"] for item in searched.json()["items"]] == [1]


async def test_retention_is_terminal_only_idempotent_and_search_excludes_redacted(
    system: System,
) -> None:
    conversation = await transition_to_greeting(system)
    headers = {"X-Service-Token": "test-token"}
    response = await system.conversation.post(
        f"/v1/conversations/{system.conversation_id}/turns",
        headers=headers,
        json={
            "turn_id": str(uuid4()),
            "call_id": str(system.call_id),
            "expected_version": conversation["version"],
            "user_text": "Private retention phrase.",
        },
    )
    assert response.status_code == 200, response.text
    cutoff = datetime.now(UTC) + timedelta(minutes=1)
    async with system.sessions.begin() as session:
        dry_run = await ConversationService(session).redact_expired_content(
            cutoff=cutoff, reason="test-retention", dry_run=True
        )
        assert dry_run.messages_redacted == 0
        assert dry_run.segments_redacted == 0
    async with system.sessions.begin() as session:
        await session.execute(
            update(Conversation)
            .where(Conversation.id == system.conversation_id)
            .values(state="COMPLETED", completed_at=datetime.now(UTC) - timedelta(days=10))
        )
    async with system.sessions.begin() as session:
        result = await ConversationService(session).redact_expired_content(
            cutoff=cutoff, reason="test-retention", batch_size=10
        )
        assert result.messages_redacted == 1
        assert result.segments_redacted == 1
    async with system.sessions() as session:
        message = await session.scalar(
            select(Message).where(Message.conversation_id == system.conversation_id)
        )
        segment = await session.scalar(
            select(TranscriptSegment).where(
                TranscriptSegment.conversation_id == system.conversation_id
            )
        )
        assert message is not None and message.text == "[REDACTED]"
        assert message.redacted_at is not None
        assert message.redaction_reason == "test-retention"
        assert message.content_sha256 == sha256(b"Private retention phrase.").hexdigest()
        assert segment is not None and segment.text == "[REDACTED]"
    repeated = await system.conversation.get(
        f"/v1/conversations/{system.conversation_id}/history", headers=headers
    )
    assert repeated.status_code == 200
    assert repeated.json()["items"][0]["redacted"] is True
    assert repeated.json()["items"][0]["text"] == "[REDACTED]"
    hidden = await system.conversation.get(
        f"/v1/conversations/{system.conversation_id}/history",
        headers=headers,
        params={"query": "Private retention phrase"},
    )
    assert hidden.status_code == 200
    assert hidden.json()["items"] == []
    async with system.sessions.begin() as session:
        result = await ConversationService(session).redact_expired_content(
            cutoff=cutoff, reason="test-retention", batch_size=10
        )
        assert result.messages_redacted == 0
        assert result.segments_redacted == 0


async def test_retry_rejects_changed_facts_before_the_first_lead_update(
    system: System,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation = await transition_to_greeting(system)
    boundary = system.conversation_app.state.lead_client
    original = boundary.update_qualification

    async def unavailable(**_: Any) -> None:
        raise LeadServiceUnavailableError("before qualification commit")

    monkeypatch.setattr(boundary, "update_qualification", unavailable)
    turn = {
        "turn_id": str(uuid4()),
        "call_id": str(system.call_id),
        "expected_version": conversation["version"],
        "user_text": "My degree is masters.",
        "facts": [{"field_key": "education_level", "value": "masters", "status": "CONFIRMED"}],
    }
    headers = {"X-Service-Token": "test-token"}
    failed = await system.conversation.post(
        f"/v1/conversations/{system.conversation_id}/turns", headers=headers, json=turn
    )
    assert failed.status_code == 503
    monkeypatch.setattr(boundary, "update_qualification", original)
    changed = turn | {
        "facts": [{"field_key": "education_level", "value": "bachelors", "status": "CONFIRMED"}]
    }
    rejected = await system.conversation.post(
        f"/v1/conversations/{system.conversation_id}/turns", headers=headers, json=changed
    )
    assert rejected.status_code == 409
    async with system.sessions() as session:
        assert await session.get(LeadScore, system.lead_id) is None


async def test_late_duplicate_failure_cannot_reset_an_applied_turn(system: System) -> None:
    conversation = await transition_to_greeting(system)
    response = await system.conversation.post(
        f"/v1/conversations/{system.conversation_id}/turns",
        headers={"X-Service-Token": "test-token"},
        json={
            "turn_id": str(uuid4()),
            "call_id": str(system.call_id),
            "expected_version": conversation["version"],
            "user_text": "My degree is masters.",
            "facts": [{"field_key": "education_level", "value": "masters", "status": "CONFIRMED"}],
        },
    )
    assert response.status_code == 200, response.text
    async with system.sessions.begin() as session:
        await ConversationService(session).mark_turn_pending(
            system.conversation_id,
            UUID(response.json()["message_id"]),
            LeadServiceUnavailableError("late failure from a duplicate request"),
        )
    async with system.sessions() as session:
        message = await session.get(Message, UUID(response.json()["message_id"]))
        assert message is not None and message.turn_status == "APPLIED"
        assert message.qualification_error is None


async def test_pending_turn_blocks_a_new_turn_with_controlled_conflict(
    system: System,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation = await transition_to_greeting(system)

    async def unavailable(**_: Any) -> None:
        raise LeadServiceUnavailableError("before qualification read")

    monkeypatch.setattr(system.conversation_app.state.lead_client, "get_qualification", unavailable)
    turn = {
        "turn_id": str(uuid4()),
        "call_id": str(system.call_id),
        "expected_version": conversation["version"],
        "user_text": "Please help.",
    }
    headers = {"X-Service-Token": "test-token"}
    assert (
        await system.conversation.post(
            f"/v1/conversations/{system.conversation_id}/turns", headers=headers, json=turn
        )
    ).status_code == 503
    conflict = await system.conversation.post(
        f"/v1/conversations/{system.conversation_id}/turns",
        headers=headers,
        json=turn | {"turn_id": str(uuid4())},
    )
    assert conflict.status_code == 409


async def test_call_failure_has_durable_structured_reason(system: System) -> None:
    await transition_to_greeting(system)
    reason = "transport_connection_lost"
    failed = await system.conversation.post(
        f"/v1/conversations/{system.conversation_id}/calls/{system.call_id}/transitions",
        headers={"X-Service-Token": "test-token", "X-Request-ID": "call-failure-test"},
        json={"target_status": "FAILED", "expected_version": 3, "reason": reason},
    )
    assert failed.status_code == 200, failed.text
    async with system.sessions() as session:
        call = await session.get(Call, system.call_id)
        assert call is not None and call.failure_reason == reason and call.failed_at is not None
        event = await session.scalar(
            select(DomainEvent).where(
                DomainEvent.aggregate_id == system.call_id,
                DomainEvent.aggregate_version == 4,
            )
        )
        assert event is not None and isinstance(event.payload, dict)
        assert event.payload["failure"]["reason"] == reason
        assert event.payload["failure"]["from_status"] == "CONNECTED"
        assert event.request_id == "call-failure-test"


@pytest.mark.failure
@pytest.mark.parametrize("phase", ["read", "update", "lost_response", "finalize"])
async def test_partial_turn_recovers_without_duplicate_effects_or_local_score(
    system: System,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    conversation = await transition_to_greeting(system)
    boundary = system.conversation_app.state.lead_client
    turn_id = uuid4()
    turn = {
        "turn_id": str(turn_id),
        "call_id": str(system.call_id),
        "expected_version": conversation["version"],
        "user_text": "I confirm my masters degree.",
        "facts": [{"field_key": "education_level", "value": "masters", "status": "CONFIRMED"}],
    }
    original_update = boundary.update_qualification
    original_finalize = ConversationService.finalize_turn

    async def dependency_failure(**kwargs: Any) -> None:
        if phase == "lost_response":
            await original_update(**kwargs)  # Lead committed, but its reply was lost.
        raise LeadServiceUnavailableError("simulated dependency failure")

    async def finalize_failure(self: ConversationService, *args: Any) -> None:
        await original_finalize(self, *args)
        raise DatabaseTimeoutError("simulated failure before Conversation COMMIT")

    headers = {"X-Service-Token": "test-token", "X-Request-ID": "partial-turn-test"}
    with monkeypatch.context() as patch:
        if phase == "finalize":
            patch.setattr(ConversationService, "finalize_turn", finalize_failure)
        else:
            method = "get_qualification" if phase == "read" else "update_qualification"
            patch.setattr(boundary, method, dependency_failure)
        failed = await system.conversation.post(
            f"/v1/conversations/{system.conversation_id}/turns", headers=headers, json=turn
        )
    assert failed.status_code == 503, failed.text
    assert "score" not in failed.json() and "qualification" not in failed.json()
    async with system.sessions() as session:
        message = await session.get(Message, turn_id)
        assert message is not None and message.turn_status == "PENDING"
        current = await session.get(Conversation, system.conversation_id)
        call = await session.get(Call, system.call_id)
        assert current is not None and current.state == "GREETING" and current.version == 3
        assert call is not None and call.status == "CONNECTED" and call.version == 3
        score = await session.get(LeadScore, system.lead_id)
        if phase in {"lost_response", "finalize"}:
            assert score is not None and score.score == 10
        else:
            assert score is None

    # Identical replay works even when Lead already committed the update.
    for _ in range(2):
        retried = await system.conversation.post(
            f"/v1/conversations/{system.conversation_id}/turns", headers=headers, json=turn
        )
        assert retried.status_code == 200, retried.text
        assert retried.json()["qualification"]["score"]["score"] == 10
        assert retried.json()["conversation"]["state"] == "DISCOVERY"
        assert retried.json()["conversation"]["version"] == 4
    async with system.sessions() as session:
        for model in (Message, TranscriptSegment):
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(model)
                    .where(model.conversation_id == system.conversation_id)
                )
                == 1
            )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(LeadScoreHistory)
                .where(LeadScoreHistory.lead_id == system.lead_id)
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(DomainEvent)
                .where(DomainEvent.causation_id == turn_id)
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(DomainEvent)
                .where(DomainEvent.aggregate_id == turn_id)
            )
            == 2
        )


async def test_concurrent_duplicate_turns_share_one_qualification_and_state_update(
    system: System,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation = await transition_to_greeting(system)
    boundary = system.conversation_app.state.lead_client
    original = boundary.update_qualification
    both_started = asyncio.Event()
    requests = 0

    async def simultaneous(**kwargs: Any) -> Any:
        nonlocal requests
        requests += 1
        if requests == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=3)
        return await original(**kwargs)

    monkeypatch.setattr(boundary, "update_qualification", simultaneous)
    turn_id = uuid4()
    turn = {
        "turn_id": str(turn_id),
        "call_id": str(system.call_id),
        "expected_version": conversation["version"],
        "user_text": "Masters degree confirmed.",
        "facts": [{"field_key": "education_level", "value": "masters", "status": "CONFIRMED"}],
    }
    responses = await asyncio.gather(
        *(
            system.conversation.post(
                f"/v1/conversations/{system.conversation_id}/turns",
                headers={"X-Service-Token": "test-token"},
                json=turn,
            )
            for _ in range(2)
        )
    )
    assert [r.status_code for r in responses] == [200, 200], [r.text for r in responses]
    async with system.sessions() as session:
        profile = await session.scalar(
            select(QualificationProfile).where(QualificationProfile.lead_id == system.lead_id)
        )
        current = await session.get(Conversation, system.conversation_id)
        assert profile is not None and profile.version == 2
        assert current is not None and current.state == "DISCOVERY" and current.version == 4
        assert (
            await session.scalar(
                select(func.count())
                .select_from(LeadScoreHistory)
                .where(LeadScoreHistory.lead_id == system.lead_id)
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(DomainEvent)
                .where(DomainEvent.aggregate_id == turn_id)
            )
            == 2
        )


@pytest.mark.parametrize("changed", ["call", "facts", "text"])
async def test_applied_turn_replay_cannot_change_input(system: System, changed: str) -> None:
    conversation = await transition_to_greeting(system)
    turn = {
        "turn_id": str(uuid4()),
        "call_id": str(system.call_id),
        "expected_version": conversation["version"],
        "user_text": "I confirm masters.",
        "facts": [{"field_key": "education_level", "value": "masters", "status": "CONFIRMED"}],
    }
    headers = {"X-Service-Token": "test-token"}
    accepted = await system.conversation.post(
        f"/v1/conversations/{system.conversation_id}/turns", headers=headers, json=turn
    )
    assert accepted.status_code == 200, accepted.text
    if changed == "call":
        other_call = uuid4()
        async with system.sessions.begin() as session:
            session.add(Call(id=other_call, conversation_id=system.conversation_id, status="ENDED"))
        turn["call_id"] = str(other_call)
    elif changed == "facts":
        turn["facts"] = []
    else:
        turn["user_text"] = "I confirm bachelors."
    rejected = await system.conversation.post(
        f"/v1/conversations/{system.conversation_id}/turns", headers=headers, json=turn
    )
    assert rejected.status_code == 409


async def test_concurrent_active_conversation_creation_is_database_enforced(system: System) -> None:
    headers = {"X-Service-Token": "test-token"}
    lead_response = await system.lead.post(
        "/v1/leads",
        headers=headers,
        json={
            "display_name": "Concurrent creation",
            "synthetic_profile_key": f"concurrent-{uuid4().hex}",
        },
    )
    lead_id = UUID(lead_response.json()["id"])
    try:
        responses = await asyncio.gather(
            *(
                system.conversation.post(
                    "/v1/conversations", headers=headers, json={"lead_id": str(lead_id)}
                )
                for _ in range(6)
            )
        )
        assert sorted(r.status_code for r in responses) == [201, 409, 409, 409, 409, 409]
        async with system.sessions() as session:
            ids = list(
                await session.scalars(
                    select(Conversation.id).where(Conversation.lead_id == lead_id)
                )
            )
            assert len(ids) == 1
        # Direct database writers are also constrained, independently of the API precheck.
        with pytest.raises(IntegrityError, match="uq_conversation_one_active_per_lead"):
            async with system.sessions.begin() as session:
                session.add(Conversation(lead_id=lead_id))
                await session.flush()
    finally:
        async with system.sessions.begin() as session:
            ids_query = select(Conversation.id).where(Conversation.lead_id == lead_id)
            await session.execute(
                delete(DomainEvent).where(DomainEvent.aggregate_id.in_(ids_query))
            )
            await session.execute(delete(Conversation).where(Conversation.lead_id == lead_id))
            await session.execute(delete(DomainEvent).where(DomainEvent.aggregate_id == lead_id))
            await session.execute(delete(Lead).where(Lead.id == lead_id))


@pytest.mark.parametrize(
    "state",
    [
        "CREATED",
        "CONNECTING",
        "GREETING",
        "DISCOVERY",
        "QUALIFICATION",
        "SCORING",
        "DECISION",
        "FOLLOW_UP",
        "HUMAN_HANDOFF",
    ],
)
async def test_every_conversation_failure_is_guarded_and_structured(
    system: System, state: str
) -> None:
    async with system.sessions.begin() as session:
        await session.execute(
            update(Conversation)
            .where(Conversation.id == system.conversation_id)
            .values(state=state)
        )
    path = f"/v1/conversations/{system.conversation_id}/transitions"
    headers = {"X-Service-Token": "test-token", "X-Request-ID": "failure-verification"}
    assert (
        await system.conversation.post(
            path,
            headers=headers,
            json={
                "target_state": "FAILED",
                "expected_version": 1,
            },
        )
    ).status_code == 422
    failure = {
        "target_state": "FAILED",
        "expected_version": 1,
        "reason": "dependency_timeout",
        "context": {"dependency": "lead-service", "retryable": True},
    }
    assert (
        await system.conversation.post(
            path, headers=headers, json=failure | {"expected_version": 2}
        )
    ).status_code == 409
    failed = await system.conversation.post(path, headers=headers, json=failure)
    assert failed.status_code == 200, failed.text
    assert failed.json()["failure_reason"] == failure["reason"]
    assert failed.json()["failure_context"] == failure["context"]
    async with system.sessions() as session:
        current = await session.get(Conversation, system.conversation_id)
        assert (
            current is not None and current.state == "FAILED" and current.completed_at is not None
        )
        event = await session.scalar(
            select(DomainEvent).where(
                DomainEvent.aggregate_id == system.conversation_id,
                DomainEvent.aggregate_version == 2,
            )
        )
        assert event is not None and isinstance(event.payload, dict)
        assert event.payload["failure"] == {
            "reason": failure["reason"],
            "context": failure["context"],
            "from_state": state,
        }
    # Failure is final; neither a second failure nor another state can rewrite it.
    for target in ("FAILED", "CONNECTING"):
        assert (
            await system.conversation.post(
                path,
                headers=headers,
                json=failure
                | {
                    "target_state": target,
                    "expected_version": 2,
                },
            )
        ).status_code == 409


@pytest.mark.parametrize(
    "call_status", ["CREATED", "CONNECTING", "CONNECTED", "RECONNECTING", "ENDED", "FAILED"]
)
async def test_call_failure_guards_follow_the_existing_graph(
    system: System, call_status: str
) -> None:
    async with system.sessions.begin() as session:
        await session.execute(
            update(Call).where(Call.id == system.call_id).values(status=call_status)
        )
    path = f"/v1/conversations/{system.conversation_id}/calls/{system.call_id}/transitions"
    headers = {"X-Service-Token": "test-token"}
    assert (
        await system.conversation.post(
            path,
            headers=headers,
            json={
                "target_status": "FAILED",
                "expected_version": 1,
            },
        )
    ).status_code == 422
    failure = {"target_status": "FAILED", "expected_version": 1, "reason": "connection_timeout"}
    assert (
        await system.conversation.post(
            path, headers=headers, json=failure | {"expected_version": 2}
        )
    ).status_code == 409
    response = await system.conversation.post(path, headers=headers, json=failure)
    allowed = call_status in {"CONNECTING", "CONNECTED", "RECONNECTING"}
    assert response.status_code == (200 if allowed else 409), response.text
    if allowed:
        async with system.sessions() as session:
            call = await session.get(Call, system.call_id)
            assert call is not None and call.failure_reason == "connection_timeout"
            assert call.failed_at is not None and call.ended_at is not None
            event = await session.scalar(
                select(DomainEvent).where(
                    DomainEvent.aggregate_id == system.call_id,
                    DomainEvent.aggregate_version == 2,
                )
            )
            assert event is not None and isinstance(event.payload, dict)
            assert event.payload["failure"]["from_status"] == call_status
            assert event.payload["failure"]["reason"] == "connection_timeout"


@pytest.mark.failure
@pytest.mark.parametrize(
    "response_kind", ["invalid_json", "invalid_schema", "wrong_lead", "no_score"]
)
async def test_invalid_authoritative_response_leaves_recoverable_turn(
    system: System, monkeypatch: pytest.MonkeyPatch, response_kind: str
) -> None:
    conversation = await transition_to_greeting(system)
    boundary = system.conversation_app.state.lead_client
    original_request = boundary.client.request
    turn_id = uuid4()
    turn = {
        "turn_id": str(turn_id),
        "call_id": str(system.call_id),
        "expected_version": conversation["version"],
        "user_text": "I confirm my masters degree.",
        "facts": [{"field_key": "education_level", "value": "masters", "status": "CONFIRMED"}],
    }

    async def incompatible(method: str, path: str, **kwargs: Any) -> httpx.Response:
        if method == "GET" and response_kind == "invalid_json":
            return httpx.Response(200, text="upstream private invalid JSON")
        if method == "GET" and response_kind == "invalid_schema":
            return httpx.Response(200, json={"private": "invalid qualification"})
        response = await original_request(method, path, **kwargs)
        payload = response.json()
        if response_kind == "wrong_lead":
            payload["lead_id"] = str(uuid4())
        elif method == "POST" and response_kind == "no_score":
            payload["score"] = None  # Mutation committed; its reply is incompatible.
        return httpx.Response(response.status_code, json=payload)

    headers = {"X-Service-Token": "test-token"}
    with monkeypatch.context() as patch:
        patch.setattr(boundary.client, "request", incompatible)
        failed = await system.conversation.post(
            f"/v1/conversations/{system.conversation_id}/turns", headers=headers, json=turn
        )
    assert failed.status_code == 503, failed.text
    assert failed.json() == {"detail": "lead service unavailable"}
    async with system.sessions() as session:
        message = await session.get(Message, turn_id)
        assert message is not None and message.turn_status == "PENDING"
        score = await session.get(LeadScore, system.lead_id)
        if response_kind == "no_score":
            assert score is not None and score.score == 10
        else:
            assert score is None
    recovered = await system.conversation.post(
        f"/v1/conversations/{system.conversation_id}/turns", headers=headers, json=turn
    )
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["qualification"]["score"]["score"] == 10
    async with system.sessions() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(LeadScoreHistory)
                .where(LeadScoreHistory.lead_id == system.lead_id)
            )
            == 1
        )


@pytest.mark.failure
@pytest.mark.parametrize("failure_kind", ["connection_refused", "read_timeout", "deadline"])
async def test_lead_transport_failure_never_returns_a_stale_or_local_score(
    system: System, monkeypatch: pytest.MonkeyPatch, failure_kind: str
) -> None:
    conversation = await transition_to_greeting(system)
    headers = {"X-Service-Token": "test-token"}
    path = f"/v1/conversations/{system.conversation_id}"
    applied_turn = {
        "turn_id": str(uuid4()),
        "call_id": str(system.call_id),
        "expected_version": conversation["version"],
        "user_text": "My degree is masters.",
        "facts": [{"field_key": "education_level", "value": "masters", "status": "CONFIRMED"}],
    }
    first = await system.conversation.post(f"{path}/turns", headers=headers, json=applied_turn)
    assert first.status_code == 200, first.text
    pending_turn = applied_turn | {
        "turn_id": str(uuid4()),
        "expected_version": first.json()["conversation"]["version"],
        "user_text": "I speak advanced English.",
        "facts": [{"field_key": "english_level", "value": "advanced", "status": "CONFIRMED"}],
    }
    boundary = system.conversation_app.state.lead_client

    async def unavailable(*_: Any, **__: Any) -> httpx.Response:
        if failure_kind == "deadline":
            await asyncio.sleep(10)
        if failure_kind == "read_timeout":
            raise httpx.ReadTimeout("private downstream timeout detail")
        raise httpx.ConnectError("private downstream connection detail")

    with monkeypatch.context() as patch:
        patch.setattr(boundary.client, "request", unavailable)
        patch.setattr(boundary, "timeout_seconds", 0.02)
        for turn in (pending_turn, applied_turn):
            failed = await system.conversation.post(f"{path}/turns", headers=headers, json=turn)
            assert failed.status_code == 503, failed.text
            assert failed.json() == {"detail": "lead service unavailable"}
        live = await system.conversation.get(f"{path}/live-state", headers=headers)
        assert live.status_code == 503
        history = await system.conversation.get(f"{path}/history", headers=headers)
        assert history.status_code == 200
        assert [m["turn_status"] for m in history.json()["items"]] == ["APPLIED", "PENDING"]
    async with system.sessions() as session:
        score = await session.get(LeadScore, system.lead_id)
        current = await session.get(Conversation, system.conversation_id)
        assert score is not None and score.score == 10
        assert current is not None and current.state == "DISCOVERY" and current.version == 4
    recovered = await system.conversation.post(f"{path}/turns", headers=headers, json=pending_turn)
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["qualification"]["score"]["score"] == 20


@pytest.mark.parametrize("terminal_state", ["COMPLETED", "FAILED"])
async def test_terminal_pending_turn_blocks_replacement_and_retention_until_recovery(
    system: System, monkeypatch: pytest.MonkeyPatch, terminal_state: str
) -> None:
    conversation = await transition_to_greeting(system)
    headers = {"X-Service-Token": "test-token"}
    path = f"/v1/conversations/{system.conversation_id}"
    turn = {
        "turn_id": str(uuid4()),
        "call_id": str(system.call_id),
        "expected_version": conversation["version"],
        "user_text": "Masters degree recovery input.",
        "facts": [{"field_key": "education_level", "value": "masters", "status": "CONFIRMED"}],
    }

    async def unavailable(**_: Any) -> None:
        raise LeadServiceUnavailableError("qualification has not committed")

    with monkeypatch.context() as patch:
        patch.setattr(
            system.conversation_app.state.lead_client, "update_qualification", unavailable
        )
        failed = await system.conversation.post(f"{path}/turns", headers=headers, json=turn)
    assert failed.status_code == 503
    async with system.sessions.begin() as session:
        await session.execute(
            update(Conversation)
            .where(Conversation.id == system.conversation_id)
            .values(
                state=terminal_state, version=4, completed_at=datetime.now(UTC) - timedelta(days=10)
            )
        )
    forbidden_failure = await system.conversation.post(
        f"{path}/transitions",
        headers=headers,
        json={"target_state": "FAILED", "expected_version": 4, "reason": "cannot_rewrite_terminal"},
    )
    assert forbidden_failure.status_code == 409
    ended = await system.conversation.post(
        f"{path}/calls/{system.call_id}/transitions",
        headers=headers,
        json={"target_status": "ENDED", "expected_version": 3},
    )
    assert ended.status_code == 200, ended.text
    replacement = await system.conversation.post(
        "/v1/conversations", headers=headers, json={"lead_id": str(system.lead_id)}
    )
    assert replacement.status_code == 409
    cutoff = datetime.now(UTC)
    async with system.sessions.begin() as session:
        result = await ConversationService(session).redact_expired_content(
            cutoff=cutoff, reason="pending-recovery-test"
        )
        assert result.messages_redacted == result.segments_redacted == 0
    recovered = await system.conversation.post(f"{path}/turns", headers=headers, json=turn)
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["conversation"]["state"] == terminal_state
    assert recovered.json()["conversation"]["version"] == 4
    assert recovered.json()["turn_status"] == "APPLIED"
    assert recovered.json()["qualification"]["score"]["score"] == 10
    async with system.sessions.begin() as session:
        result = await ConversationService(session).redact_expired_content(
            cutoff=cutoff, reason="pending-recovery-test"
        )
        assert result.messages_redacted == result.segments_redacted == 1
    # Replay validates its durable fingerprint even after text and metadata redaction.
    replay = await system.conversation.post(f"{path}/turns", headers=headers, json=turn)
    assert replay.status_code == 200, replay.text
    replacement = await system.conversation.post(
        "/v1/conversations", headers=headers, json={"lead_id": str(system.lead_id)}
    )
    assert replacement.status_code == 201, replacement.text
    async with system.sessions() as session:
        message = await session.get(Message, UUID(turn["turn_id"]))
        assert (
            message is not None and message.text == "[REDACTED]" and message.message_metadata == {}
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(LeadScoreHistory)
                .where(LeadScoreHistory.lead_id == system.lead_id)
            )
            == 1
        )


async def test_legacy_qualification_receipt_replays_without_rewriting_or_crossing_scope(
    system: System,
) -> None:
    headers = {"X-Service-Token": "test-token"}
    turn_id = uuid4()
    fact = {"field_key": "education_level", "value": "masters", "status": "CONFIRMED"}
    payload = {
        "turn_id": str(turn_id),
        "conversation_id": str(system.conversation_id),
        "expected_profile_version": 1,
        "facts": [fact],
    }
    path = f"/v1/leads/{system.lead_id}/qualification/updates"
    first = await system.lead.post(path, headers=headers, json=payload)
    assert first.status_code == 200, first.text
    async with system.sessions.begin() as session:
        event = await session.scalar(select(DomainEvent).where(DomainEvent.causation_id == turn_id))
        assert event is not None and isinstance(event.payload, dict)
        legacy_facts = [fact | {"confidence": None, "resolve_conflict": False}]
        legacy_hash = sha256(
            json.dumps(legacy_facts, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        event.payload = event.payload | {"request_hash": legacy_hash}
    replay = await system.lead.post(path, headers=headers, json=payload)
    assert replay.status_code == 200, replay.text
    assert replay.json()["version"] == first.json()["version"] == 2
    assert replay.json()["score"] == first.json()["score"]
    wrong_scope = await system.lead.post(
        path, headers=headers, json=payload | {"conversation_id": str(uuid4())}
    )
    assert wrong_scope.status_code == 409
    async with system.sessions() as session:
        event = await session.scalar(select(DomainEvent).where(DomainEvent.causation_id == turn_id))
        assert event is not None and isinstance(event.payload, dict)
        assert event.payload["request_hash"] == legacy_hash


async def test_definitively_rejected_facts_do_not_permanently_block_the_conversation(
    system: System,
) -> None:
    conversation = await transition_to_greeting(system)
    headers = {"X-Service-Token": "test-token"}
    path = f"/v1/conversations/{system.conversation_id}/turns"
    rejected_turn = {
        "turn_id": str(uuid4()),
        "call_id": str(system.call_id),
        "expected_version": conversation["version"],
        "user_text": "Please check my education.",
        "facts": [
            {"field_key": "education_level", "value": "invalid-degree", "status": "CONFIRMED"}
        ],
    }
    rejected = await system.conversation.post(path, headers=headers, json=rejected_turn)
    assert rejected.status_code == 422, rejected.text
    history = await system.conversation.get(
        f"/v1/conversations/{system.conversation_id}/history", headers=headers
    )
    assert history.json()["items"][0]["turn_status"] == "FAILED"
    async with system.sessions() as session:
        assert await session.get(LeadScore, system.lead_id) is None
    corrected = await system.conversation.post(
        path,
        headers=headers,
        json=rejected_turn
        | {
            "turn_id": str(uuid4()),
            "facts": [{"field_key": "education_level", "value": "masters", "status": "CONFIRMED"}],
        },
    )
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["qualification"]["score"]["score"] == 10
    # A later ambiguous error from an old duplicate cannot make rejected input pending again.
    async with system.sessions.begin() as session:
        await ConversationService(session).mark_turn_pending(
            system.conversation_id,
            UUID(str(rejected_turn["turn_id"])),
            LeadServiceUnavailableError("late duplicate request failure"),
        )
    history = await system.conversation.get(
        f"/v1/conversations/{system.conversation_id}/history", headers=headers
    )
    assert [m["turn_status"] for m in history.json()["items"]] == ["FAILED", "APPLIED"]


def agent_body(system: System, version: int, **changes: Any) -> dict[str, Any]:
    return {
        "message_id": str(uuid4()),
        "call_id": str(system.call_id),
        "expected_version": version,
        "text": "Hello, how can I help?",
        "provider": "mock",
        "model": "mock-v1",
        **changes,
    }


async def post_agent(system: System, body: dict[str, Any]) -> httpx.Response:
    return await system.conversation.post(
        f"/v1/conversations/{system.conversation_id}/agent-messages",
        headers={"X-Service-Token": "test-token", "X-Request-ID": "agent-test"},
        json=body,
    )


async def test_agent_output_is_atomic_and_does_not_invoke_lead(system: System) -> None:
    current = await transition_to_greeting(system)
    # Deliberately remove the dependency: this endpoint must not need Lead.
    system.conversation_app.state.lead_client = None
    body = agent_body(system, current["version"])
    response = await post_agent(system, body)
    assert response.status_code == 200, response.text
    assert response.json()["speaker"] == "AGENT"
    assert response.json()["turn_status"] == "APPLIED"
    assert response.json()["message_metadata"]["output_kind"] == "generated"
    async with system.sessions() as session:
        conversation = await session.get(Conversation, system.conversation_id)
        assert conversation is not None
        assert (conversation.state, conversation.version) == ("GREETING", current["version"])
        assert conversation.last_turn_at is None
        segment = await session.scalar(
            select(TranscriptSegment).where(
                TranscriptSegment.conversation_id == system.conversation_id
            )
        )
        assert segment is not None and segment.speaker == "AGENT"
        assert segment.provider_segment_id == body["message_id"]
        assert segment.sequence_number == response.json()["sequence_number"]
        event = await session.scalar(
            select(DomainEvent).where(
                DomainEvent.idempotency_key == f"conversation.agent.recorded:{body['message_id']}"
            )
        )
        assert event is not None and event.request_id == "agent-test"
        assert "Hello" not in json.dumps(event.payload)
        assert (
            await session.scalar(
                select(func.count())
                .select_from(LeadScoreHistory)
                .where(LeadScoreHistory.lead_id == system.lead_id)
            )
            == 0
        )


async def test_agent_concurrent_lost_response_retry_creates_one_effect(system: System) -> None:
    current = await transition_to_greeting(system)
    body = agent_body(system, current["version"])
    replies = await asyncio.gather(*(post_agent(system, body) for _ in range(6)))
    assert all(reply.status_code == 200 for reply in replies)
    assert len({reply.json()["id"] for reply in replies}) == 1
    # Admission version is not part of replay identity.
    retry = await post_agent(system, body | {"expected_version": 1})
    assert retry.status_code == 200
    async with system.sessions() as session:
        for model in (Message, TranscriptSegment):
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(model)
                    .where(model.conversation_id == system.conversation_id)
                )
                == 1
            )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(DomainEvent)
                .where(DomainEvent.aggregate_id == UUID(body["message_id"]))
            )
            == 1
        )


@pytest.mark.parametrize(
    "change",
    [
        {"text": "Changed response"},
        {"provider": "other"},
        {"model": "other"},
        {"call_id": str(uuid4())},
        {"parent_turn_id": str(uuid4())},
    ],
)
async def test_agent_changed_retry_conflicts(system: System, change: dict[str, Any]) -> None:
    current = await transition_to_greeting(system)
    body = agent_body(system, current["version"])
    assert (await post_agent(system, body)).status_code == 200
    assert (await post_agent(system, body | change)).status_code == 409


async def test_agent_replay_after_terminal_redaction_does_not_restore_text(system: System) -> None:
    current = await transition_to_greeting(system)
    body = agent_body(system, current["version"])
    assert (await post_agent(system, body)).status_code == 200
    headers = {"X-Service-Token": "test-token"}
    failed = await system.conversation.post(
        f"/v1/conversations/{system.conversation_id}/transitions",
        headers=headers,
        json={"target_state": "FAILED", "expected_version": current["version"], "reason": "test"},
    )
    assert failed.status_code == 200
    async with system.sessions.begin() as session:
        await ConversationService(session).redact_expired_content(
            cutoff=datetime.now(UTC) + timedelta(seconds=1)
        )
    replay = await post_agent(system, body)
    assert replay.status_code == 200 and replay.json()["text"] == "[REDACTED]"
    assert replay.json()["redacted"] is True
    assert (await post_agent(system, body | {"message_id": str(uuid4())})).status_code == 409


async def test_agent_requires_connected_call_and_current_version(system: System) -> None:
    assert (await post_agent(system, agent_body(system, 1))).status_code == 409
    current = await transition_to_greeting(system)
    assert (await post_agent(system, agent_body(system, 1))).status_code == 409
    async with system.sessions.begin() as session:
        call = await session.get(Call, system.call_id)
        assert call is not None
        call.status = "RECONNECTING"
    assert (await post_agent(system, agent_body(system, current["version"]))).status_code == 409


async def test_agent_reply_requires_latest_applied_user_and_cannot_overtake_pending(
    system: System,
) -> None:
    current = await transition_to_greeting(system)
    turn_id = uuid4()
    user = {
        "turn_id": str(turn_id),
        "call_id": str(system.call_id),
        "expected_version": current["version"],
        "user_text": "Tell me more",
    }
    # Persist only, to reproduce the durable window before Lead/finalization.
    from voice_platform_contracts.conversation import ConversationTurn

    async with system.sessions.begin() as session:
        await ConversationService(session).persist_turn(
            system.conversation_id, ConversationTurn.model_validate(user)
        )
    body = agent_body(system, current["version"], parent_turn_id=str(turn_id))
    assert (await post_agent(system, body)).status_code == 409
    applied = await system.conversation.post(
        f"/v1/conversations/{system.conversation_id}/turns",
        headers={"X-Service-Token": "test-token"},
        json=user,
    )
    assert applied.status_code == 200
    version = applied.json()["conversation"]["version"]
    assert (await post_agent(system, body | {"expected_version": version})).status_code == 200
    assert (await post_agent(system, agent_body(system, version))).status_code == 409
    assert (
        await post_agent(system, agent_body(system, version, parent_turn_id=str(uuid4())))
    ).status_code == 409
    # Agent IDs cannot reuse a USER record even with identical text.
    collision = agent_body(system, version, message_id=str(turn_id), text=user["user_text"])
    assert (await post_agent(system, collision)).status_code == 409


async def test_agent_rollback_and_database_failure_recover_without_partial_effects(
    system: System,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = await transition_to_greeting(system)
    body = agent_body(system, current["version"])
    original = ConversationService.persist_agent_message

    async def interrupted(self: ConversationService, *args: Any, **kwargs: Any) -> Message:
        await original(self, *args, **kwargs)
        raise DatabaseTimeoutError("private database details")

    monkeypatch.setattr(ConversationService, "persist_agent_message", interrupted)
    failure = await post_agent(system, body)
    assert failure.status_code == 503 and "private" not in failure.text
    async with system.sessions() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.conversation_id == system.conversation_id)
            )
            == 0
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(TranscriptSegment)
                .where(TranscriptSegment.conversation_id == system.conversation_id)
            )
            == 0
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(DomainEvent)
                .where(DomainEvent.aggregate_id == UUID(body["message_id"]))
            )
            == 0
        )
    monkeypatch.setattr(ConversationService, "persist_agent_message", original)
    assert (await post_agent(system, body)).status_code == 200


async def test_agent_endpoint_validates_auth_and_redacts_invalid_input(system: System) -> None:
    path = f"/v1/conversations/{system.conversation_id}/agent-messages"
    assert (await system.conversation.post(path, json={})).status_code == 401
    invalid = await post_agent(system, agent_body(system, 1, text="private\x00text"))
    assert invalid.status_code == 422 and "private" not in invalid.text
    missing = await system.conversation.post(
        f"/v1/conversations/{uuid4()}/agent-messages",
        headers={"X-Service-Token": "test-token"},
        json=agent_body(system, 1),
    )
    assert missing.status_code == 404


async def test_voice_dialogue_uses_durable_history_and_separate_agent_path(system: System) -> None:
    from voice_platform_llm import LLMRouter, MockLLMProvider, MockScript, ProviderSlot
    from voice_platform_runtime.backend import Backend
    from voice_platform_runtime.dialogue import Dialogue

    await transition_to_greeting(system)
    dialogue = Dialogue(
        Backend(system.conversation, "test-token"),
        LLMRouter(
            (ProviderSlot(MockLLMProvider(MockScript(text="Which country interests you?"))),)
        ),
        system.conversation_id,
        system.call_id,
    )
    greeting = await dialogue.greeting()
    assert greeting is not None
    assert await dialogue.greeting() is None
    turn_id = uuid4()
    text, mid = await dialogue.reply("I would like to learn about immigration", turn_id)
    assert text == "Which country interests you?"
    async with system.sessions() as session:
        messages = (
            await session.scalars(
                select(Message)
                .where(Message.conversation_id == system.conversation_id)
                .order_by(Message.sequence_number)
            )
        ).all()
        assert [m.speaker for m in messages] == ["AGENT", "USER", "AGENT"]
        assert messages[1].id == turn_id and messages[2].id == mid
        assert all(m.turn_status == "APPLIED" for m in messages)
        assert messages[1].message_metadata == {"qualification_facts": []}
    assert dialogue.pending is None


async def test_voice_dependency_failure_retries_original_turn_without_regeneration(
    system: System,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from voice_platform_llm import LLMRouter, MockLLMProvider, ProviderSlot
    from voice_platform_runtime.backend import Backend, DependencyError
    from voice_platform_runtime.dialogue import Dialogue

    await transition_to_greeting(system)
    dialogue = Dialogue(
        Backend(system.conversation, "test-token"),
        LLMRouter((ProviderSlot(MockLLMProvider()),)),
        system.conversation_id,
        system.call_id,
    )
    original = system.conversation_app.state.lead_client

    class Offline:
        async def get_qualification(self, **_: Any) -> Any:
            raise LeadServiceUnavailableError("private lead details")

    system.conversation_app.state.lead_client = Offline()
    uid = uuid4()
    with pytest.raises(DependencyError):
        await dialogue.reply("Information please", uid)
    assert dialogue.pending is not None
    async with system.sessions() as session:
        user = await session.get(Message, uid)
        assert user is not None and user.turn_status == "PENDING"
        assert (
            await session.scalar(
                select(func.count())
                .select_from(Message)
                .where(
                    Message.conversation_id == system.conversation_id, Message.speaker == "AGENT"
                )
            )
            == 0
        )
    system.conversation_app.state.lead_client = original
    await dialogue.recover()
    assert dialogue.pending is None
    # Reattach after a runtime restart and recover from durable state, not an audio replay.
    other = Dialogue(
        Backend(system.conversation, "test-token"),
        dialogue.llm,
        system.conversation_id,
        system.call_id,
    )
    await other.recover()
    async with system.sessions() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.conversation_id == system.conversation_id)
            )
            == 1
        )


async def test_voice_cancel_during_generation_preserves_user_without_agent_output(
    system: System,
) -> None:
    from voice_platform_llm import LLMRouter, MockLLMProvider, MockScript, ProviderSlot
    from voice_platform_runtime.backend import Backend
    from voice_platform_runtime.dialogue import Dialogue

    await transition_to_greeting(system)
    dialogue = Dialogue(
        Backend(system.conversation, "test-token"),
        LLMRouter((ProviderSlot(MockLLMProvider(MockScript(delay_seconds=10))),)),
        system.conversation_id,
        system.call_id,
    )
    uid = uuid4()
    task = asyncio.create_task(dialogue.reply("Please explain", uid))
    try:
        async with asyncio.timeout(5):
            while True:
                async with system.sessions() as session:
                    user = await session.get(Message, uid)
                    if user is not None and user.turn_status == "APPLIED":
                        break
                await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        if not task.done():
            task.cancel()
    async with system.sessions() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.conversation_id == system.conversation_id)
            )
            == 1
        )
    assert dialogue.pending is None


async def test_voice_lost_agent_reply_recovers_same_output_id_without_duplicate(
    system: System,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from voice_platform_contracts.conversation import AgentMessageCreate, MessageHistoryEntry
    from voice_platform_llm import LLMRouter, MockLLMProvider, ProviderSlot
    from voice_platform_runtime.backend import Backend, DependencyError
    from voice_platform_runtime.dialogue import Dialogue

    await transition_to_greeting(system)
    backend = Backend(system.conversation, "test-token")
    original = backend.agent

    async def lost(cid: UUID, payload: AgentMessageCreate, rid: UUID) -> MessageHistoryEntry:
        await original(cid, payload, rid)
        raise DependencyError()

    monkeypatch.setattr(backend, "agent", lost)
    dialogue = Dialogue(
        backend,
        LLMRouter((ProviderSlot(MockLLMProvider()),)),
        system.conversation_id,
        system.call_id,
    )
    with pytest.raises(DependencyError):
        await dialogue.reply("Information please", uuid4())
    assert isinstance(dialogue.pending, AgentMessageCreate)
    mid = dialogue.pending.message_id
    monkeypatch.setattr(backend, "agent", original)
    await dialogue.recover()
    async with system.sessions() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.conversation_id == system.conversation_id)
            )
            == 2
        )
        assert await session.get(Message, mid) is not None


async def test_voice_runtime_session_lifecycle_uses_backend_call_graph(system: System) -> None:
    pytest.importorskip("pipecat")
    from voice_platform_llm import LLMSettings
    from voice_platform_runtime.app import Session, SessionCreate
    from voice_platform_runtime.backend import Backend
    from voice_platform_speech import SpeechSettings

    async with httpx.AsyncClient() as providers:
        session = Session(
            SessionCreate(conversation_id=system.conversation_id, call_id=system.call_id),
            Backend(system.conversation, "test-token"),
            SpeechSettings(),
            LLMSettings(),
            providers,
        )
        await session.prepare()
        assert session.call is not None and str(session.call.status) == "CONNECTING"
        await session.connected()
        assert str(session.call.status) == "CONNECTED" and session.processor.ready
        await session.disconnected()
        assert str(session.call.status) == "RECONNECTING" and not session.processor.ready
        await session.connected()
        assert str(session.call.status) == "CONNECTED" and session.call.reconnect_attempts == 1
        await session.close()
        assert str(session.call.status) == "ENDED"
        await session.close()


async def test_real_webrtc_audio_roundtrip_reconnect_and_transcript_persistence(
    system: System,
) -> None:
    pytest.importorskip("pipecat")
    import numpy as np
    from aiortc import AudioStreamTrack, RTCConfiguration, RTCPeerConnection, RTCSessionDescription
    from av import AudioFrame
    from voice_platform_runtime.app import create_app as create_voice_app
    from voice_platform_runtime.backend import Backend
    from voice_platform_speech import AudioFormat, fixture_pcm

    class ToneTrack(AudioStreamTrack):
        async def recv(self) -> AudioFrame:
            frame = cast(AudioFrame, await super().recv())
            frame.planes[0].update(fixture_pcm(AudioFormat(sample_rate=8000)))
            return frame

    app = create_voice_app("http://unused", "test-token")
    notifications: list[dict[str, Any]] = []
    peers: list[RTCPeerConnection] = []
    readers: list[asyncio.Task[None]] = []
    audio_heard = asyncio.Event()

    async def wait_for(check: Any) -> None:
        async with asyncio.timeout(15):
            while not check():
                await asyncio.sleep(0.02)

    async with app.router.lifespan_context(app):
        app.state.backend = Backend(system.conversation, "test-token")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://voice"
        ) as browser:
            response = await browser.post(
                "/sessions",
                json={
                    "conversation_id": str(system.conversation_id),
                    "call_id": str(system.call_id),
                    "manual_turns": True,
                },
            )
            assert response.status_code == 200, response.text
            session = response.json()
            base = f"/sessions/{session['session_id']}"
            headers = {"Authorization": "Bearer " + session["token"]}
            assert (await browser.post(base + "/offer", json={"sdp": "invalid"})).status_code == 404
            assert (
                await browser.post(
                    "/sessions",
                    json={
                        "conversation_id": str(system.conversation_id),
                        "call_id": str(system.call_id),
                    },
                )
            ).status_code == 409

            async def connect(previous_pc: str | None = None) -> tuple[Any, str]:
                pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))
                peers.append(pc)
                pc.addTrack(ToneTrack())
                channel = pc.createDataChannel("chat")

                @channel.on("message")
                def on_message(message: str) -> None:
                    notifications.append(json.loads(message))

                @pc.on("track")
                def on_track(track: Any) -> None:
                    async def read_audio() -> None:
                        try:
                            while True:
                                frame = await track.recv()
                                if np.max(np.abs(frame.to_ndarray().astype(np.int32))) > 100:
                                    audio_heard.set()
                        except Exception:
                            pass

                    readers.append(asyncio.create_task(read_audio()))

                try:
                    await pc.setLocalDescription(await pc.createOffer())
                    offer = {
                        "sdp": pc.localDescription.sdp,
                        "type": "offer",
                        "pc_id": previous_pc,
                        "restart_pc": previous_pc is not None,
                    }
                    answer = await browser.post(base + "/offer", headers=headers, json=offer)
                    assert answer.status_code == 200, answer.text
                    result = answer.json()
                    replay = await browser.post(base + "/offer", headers=headers, json=offer)
                    assert replay.status_code == 200 and replay.json() == result
                    await pc.setRemoteDescription(
                        RTCSessionDescription(sdp=result["sdp"], type="answer")
                    )
                    await wait_for(
                        lambda: (
                            channel.readyState == "open"
                            and any(n.get("type") == "connected" for n in notifications)
                        )
                    )
                    return channel, str(result["pc_id"])
                except BaseException:
                    await pc.close()
                    raise

            try:
                channel, pc_id = await connect()
                await wait_for(lambda: any(n.get("type") == "ready" for n in notifications))
                await asyncio.wait_for(audio_heard.wait(), 10)
                channel.send(json.dumps({"type": "start"}))
                await asyncio.sleep(0.3)
                channel.send(json.dumps({"type": "stop"}))
                await wait_for(
                    lambda: any(
                        n.get("type") == "transcript" and n.get("final") for n in notifications
                    )
                )
                await wait_for(
                    lambda: len([n for n in notifications if n.get("type") == "agent"]) == 2
                )
                notifications.clear()
                await peers[0].close()
                channel2, pc_id2 = await connect(pc_id)
                assert pc_id2 != pc_id  # A restarted media peer keeps the durable call ID.
                await wait_for(lambda: any(n.get("type") == "connected" for n in notifications))
                assert channel2.readyState == "open"
                ended = await browser.delete(base, headers=headers)
                assert ended.status_code == 200, ended.text
                assert (await browser.delete(base, headers=headers)).status_code == 404
            finally:
                for pc in peers:
                    await pc.close()
                for task in readers:
                    task.cancel()
                await asyncio.gather(*readers, return_exceptions=True)
    async with system.sessions() as db:
        call = await db.get(Call, system.call_id)
        assert call is not None and call.status == "ENDED" and call.reconnect_attempts >= 1
        messages = (
            await db.scalars(
                select(Message)
                .where(Message.conversation_id == system.conversation_id)
                .order_by(Message.sequence_number)
            )
        ).all()
        assert [message.speaker for message in messages] == ["AGENT", "USER", "AGENT"]
        assert (
            await db.scalar(
                select(func.count())
                .select_from(TranscriptSegment)
                .where(TranscriptSegment.conversation_id == system.conversation_id)
            )
            == 3
        )


@pytest.mark.parametrize("manual", [True, False])
async def test_chrome_browser_voice_page_audio_controls_reconnect_and_end(
    system: System,
    monkeypatch: pytest.MonkeyPatch,
    manual: bool,
) -> None:
    if os.getenv("RUN_BROWSER_VOICE_TESTS") != "1":
        pytest.skip("opt in with RUN_BROWSER_VOICE_TESTS=1 and CHROME_EXECUTABLE")
    pytest.importorskip("playwright")
    import socket
    from pathlib import Path

    import uvicorn
    from api_gateway.app import create_app as create_gateway_app
    from api_gateway.client import ConversationServiceClient, LeadServiceClient
    from api_gateway.settings import GatewaySettings
    from fastapi import FastAPI
    from playwright.async_api import async_playwright
    from starlette.routing import Mount
    from voice_platform_runtime.backend import Backend

    chrome = os.getenv(
        "CHROME_EXECUTABLE", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    )
    speech_file = os.getenv("VOICE_TEST_WAV")
    if not manual and (not speech_file or not Path(speech_file).exists()):
        pytest.fail("VOICE_TEST_WAV is required for automatic VAD browser verification")
    if not Path(chrome).exists():
        pytest.fail("CHROME_EXECUTABLE must point to an installed browser")
    monkeypatch.setenv("VOICE_RUNTIME_ENABLED", "1")
    monkeypatch.setenv("SPEECH_MODE", "mock")
    monkeypatch.setenv("LLM_MODE", "mock")
    gateway = create_gateway_app(
        settings=GatewaySettings("test", "http://lead", "test-token", 5),
        client=LeadServiceClient(system.lead, service_auth_token="test-token"),
        conversation_client=ConversationServiceClient(
            system.conversation, service_auth_token="test-token"
        ),
    )
    voice = cast(FastAPI, next(route.app for route in gateway.routes if isinstance(route, Mount)))
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(gateway, log_level="critical", access_log=False, lifespan="off")
    )
    async with gateway.router.lifespan_context(gateway):
        voice.state.backend = Backend(system.conversation, "test-token")
        task = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(10):
                while not server.started:
                    await asyncio.sleep(0.02)
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(
                    executable_path=chrome,
                    headless=True,
                    args=[
                        "--use-fake-device-for-media-stream",
                        "--use-fake-ui-for-media-stream",
                        "--autoplay-policy=no-user-gesture-required",
                        *(
                            ["--use-file-for-fake-audio-capture=" + str(speech_file)]
                            if not manual
                            else []
                        ),
                    ],
                )
                try:
                    page = await browser.new_page()
                    failures: list[str] = []
                    page.on("pageerror", lambda error: failures.append(str(error)))
                    await page.goto(f"http://127.0.0.1:{port}/voice/")
                    await page.locator("#conversation").fill(str(system.conversation_id))
                    await page.locator("#call").fill(str(system.call_id))
                    await page.locator("#manual").set_checked(manual)
                    await page.locator("#connect").click()
                    await page.wait_for_function(
                        "document.querySelector('#log').textContent.includes('\\\"type\\\":\\\"ready\\\"')",
                        timeout=20000,
                    )
                    # Synthetic microphone input never touches the user's microphone.
                    await page.wait_for_function(
                        "document.querySelector('#audio').currentTime > 0", timeout=10000
                    )
                    if manual:
                        await page.locator("#talk").click()
                        await asyncio.sleep(0.3)
                        await page.locator("#talk").click()
                    await page.wait_for_function(
                        "document.querySelector('#log').textContent.includes('\\\"type\\\":\\\"transcript\\\"')",
                        timeout=20000,
                    )
                    await page.wait_for_function(
                        "document.querySelector('#log').textContent"
                        '.split(\'\\"type\\":\\"agent\\"\').length >= 3',
                        timeout=20000,
                    )
                    await page.locator("#reconnect").click()
                    await page.wait_for_function(
                        "document.querySelector('#log').textContent"
                        '.split(\'\\"type\\":\\"connected\\"\').length >= 3',
                        timeout=20000,
                    )
                    await page.locator("#end").click()
                    await page.wait_for_function(
                        "document.querySelector('#log').textContent.includes('\\\"ended\\\":true')",
                        timeout=10000,
                    )
                    assert failures == []
                    assert "Request failed" not in await page.locator("#log").inner_text()
                finally:
                    await browser.close()
        finally:
            server.should_exit = True
            await asyncio.wait_for(task, 10)
            listener.close()
    async with system.sessions() as session:
        call = await session.get(Call, system.call_id)
        assert call is not None and call.status == "ENDED"
        assert (
            await session.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.conversation_id == system.conversation_id)
            )
            == 3
        )


async def test_gateway_agent_endpoint_preserves_payload_correlation_and_retry(
    system: System,
) -> None:
    from api_gateway.app import create_app as create_gateway_app
    from api_gateway.client import ConversationServiceClient, LeadServiceClient
    from api_gateway.settings import GatewaySettings

    current = await transition_to_greeting(system)
    gateway = create_gateway_app(
        settings=GatewaySettings("test", "http://lead", "test-token", 5),
        client=LeadServiceClient(system.lead, service_auth_token="test-token"),
        conversation_client=ConversationServiceClient(
            system.conversation, service_auth_token="test-token"
        ),
    )
    async with gateway.router.lifespan_context(gateway):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(gateway), base_url="http://gateway"
        ) as client:
            path = f"/v1/conversations/{system.conversation_id}/agent-messages"
            body = agent_body(system, current["version"])
            first = await client.post(
                path, json=body, headers={"X-Request-ID": "gateway-agent-test"}
            )
            assert first.status_code == 200
            assert first.headers["X-Request-ID"] == "gateway-agent-test"
            retry = await client.post(path, json=body)
            assert retry.json()["id"] == first.json()["id"]
            assert (await client.post(path, json=body | {"text": "changed"})).status_code == 409
            async with system.sessions() as session:
                event = await session.scalar(
                    select(DomainEvent).where(
                        DomainEvent.idempotency_key
                        == f"conversation.agent.recorded:{body['message_id']}"
                    )
                )
                assert event is not None and event.request_id == "gateway-agent-test"


async def test_voice_cancel_waits_for_durable_mutation_and_clears_pending_after_commit(
    system: System,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from voice_platform_contracts.conversation import ConversationTurn, ConversationTurnResponse
    from voice_platform_llm import LLMRouter, MockLLMProvider, ProviderSlot
    from voice_platform_runtime.backend import Backend
    from voice_platform_runtime.dialogue import Dialogue

    await transition_to_greeting(system)
    backend = Backend(system.conversation, "test-token")
    committed = asyncio.Event()
    release = asyncio.Event()
    original = backend.user

    async def delayed(cid: UUID, body: ConversationTurn, rid: UUID) -> ConversationTurnResponse:
        result = await original(cid, body, rid)
        committed.set()
        await release.wait()
        return result

    monkeypatch.setattr(backend, "user", delayed)
    dialogue = Dialogue(
        backend,
        LLMRouter((ProviderSlot(MockLLMProvider()),)),
        system.conversation_id,
        system.call_id,
    )
    uid = uuid4()
    task = asyncio.create_task(dialogue.reply("Please explain", uid))
    await asyncio.wait_for(committed.wait(), 5)
    task.cancel()
    await asyncio.sleep(0.01)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert dialogue.pending is None
    async with system.sessions() as db:
        message = await db.get(Message, uid)
        assert message is not None and message.turn_status == "APPLIED"
        assert (
            await db.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.conversation_id == system.conversation_id)
            )
            == 1
        )


async def test_voice_call_lost_transition_response_recovers_authoritative_status(
    system: System,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("pipecat")
    from voice_platform_contracts.conversation import CallResponse
    from voice_platform_llm import LLMSettings
    from voice_platform_runtime.app import Session, SessionCreate
    from voice_platform_runtime.backend import Backend, DependencyError
    from voice_platform_speech import SpeechSettings

    backend = Backend(system.conversation, "test-token")
    original = backend.call
    lost_once = False

    async def lost(
        cid: UUID, current: CallResponse, target: str, rid: UUID, reason: str | None = None
    ) -> CallResponse:
        nonlocal lost_once
        result = await original(cid, current, target, rid, reason)
        if target == "CONNECTED" and not lost_once:
            lost_once = True
            raise DependencyError()
        return result

    monkeypatch.setattr(backend, "call", lost)
    async with httpx.AsyncClient() as providers:
        session = Session(
            SessionCreate(conversation_id=system.conversation_id, call_id=system.call_id),
            backend,
            SpeechSettings(),
            LLMSettings(),
            providers,
        )
        await session.prepare()
        await session.connected()
        assert session.call is not None and session.call.status == "CONNECTED"
        assert session.processor.ready
        await session.close()
    async with system.sessions() as db:
        call = await db.get(Call, system.call_id)
        assert call is not None and call.version == 4  # one CONNECTING, CONNECTED, ENDED


async def test_voice_restart_recovers_existing_pending_user_from_durable_history(
    system: System,
) -> None:
    from voice_platform_contracts.conversation import ConversationTurn
    from voice_platform_llm import LLMRouter, MockLLMProvider, ProviderSlot
    from voice_platform_runtime.backend import Backend
    from voice_platform_runtime.dialogue import Dialogue

    current = await transition_to_greeting(system)
    uid = uuid4()
    async with system.sessions.begin() as db:
        await ConversationService(db).persist_turn(
            system.conversation_id,
            ConversationTurn(
                turn_id=uid,
                call_id=system.call_id,
                expected_version=current["version"],
                user_text="Saved before restart",
            ),
        )
    dialogue = Dialogue(
        Backend(system.conversation, "test-token"),
        LLMRouter((ProviderSlot(MockLLMProvider()),)),
        system.conversation_id,
        system.call_id,
    )
    await dialogue.recover()
    async with system.sessions() as db:
        message = await db.get(Message, uid)
        assert message is not None and message.turn_status == "APPLIED"
        assert (
            await db.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.conversation_id == system.conversation_id)
            )
            == 1
        )


async def test_voice_close_releases_media_even_when_backend_end_is_unavailable(
    system: System, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("pipecat")
    from voice_platform_llm import LLMSettings
    from voice_platform_runtime.app import Session, SessionCreate
    from voice_platform_runtime.backend import Backend, DependencyError
    from voice_platform_speech import SpeechSettings

    class MediaConnection:
        disconnected = False

        def send_app_message(self, payload: dict[str, object]) -> None:
            pass

        async def disconnect(self) -> None:
            self.disconnected = True

    backend = Backend(system.conversation, "test-token")
    media = MediaConnection()
    async with httpx.AsyncClient() as providers:
        session = Session(
            SessionCreate(conversation_id=system.conversation_id, call_id=system.call_id),
            backend,
            SpeechSettings(),
            LLMSettings(),
            providers,
        )
        await session.prepare()
        await session.connected()
        session.connection = cast(Any, media)

        async def unavailable(*_: Any, **__: Any) -> Any:
            raise DependencyError()

        with monkeypatch.context() as patch:
            patch.setattr(backend, "call", unavailable)
            patch.setattr(backend, "live", unavailable)
            with pytest.raises(DependencyError):
                await session.close()
        assert media.disconnected
        assert not session.closed  # Retain the original durable call for end retry.
        async with system.sessions() as db:
            call = await db.get(Call, system.call_id)
            assert call is not None and call.status == "CONNECTED"
        await session.close()
        assert session.closed and session.call is not None and session.call.status == "ENDED"

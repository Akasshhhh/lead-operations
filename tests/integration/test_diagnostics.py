"""Real domain persistence survives session-scoped fault and reset/recovery."""

import json
from typing import Any, cast
from uuid import uuid4

import httpx
import pytest
from api_gateway.app import create_app as gateway_app
from api_gateway.client import ConversationServiceClient, LeadServiceClient
from api_gateway.settings import GatewaySettings
from sqlalchemy import func, select
from test_conversation_service import System, transition_to_greeting
from test_conversation_service import system as system
from test_staged_qualification import ToolProvider, proposals
from voice_platform_config.observability import Telemetry
from voice_platform_db.models import LeadScoreHistory, Message
from voice_platform_llm import LLMRouter, ProviderSlot
from voice_platform_runtime.app import create_app
from voice_platform_runtime.backend import Backend, DependencyError
from voice_platform_runtime.diagnostics import FaultCreate, Faults, ObservedBackend
from voice_platform_runtime.qualified import QualifiedDialogue

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.mark.parametrize("enabled", [False, True])
async def test_fault_api_is_capability_scoped_gated_validated_and_resettable(
    system: System,
    monkeypatch: pytest.MonkeyPatch,
    enabled: bool,
) -> None:
    monkeypatch.setenv("DEMO_FAULTS_ENABLED", "1" if enabled else "0")
    app = create_app("http://conversation", "test-token")
    async with app.router.lifespan_context(app):
        app.state.backend = Backend(system.conversation, "test-token")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://voice"
        ) as client:
            session = (
                await client.post(
                    "/sessions",
                    json={
                        "conversation_id": str(system.conversation_id),
                        "call_id": str(system.call_id),
                    },
                )
            ).json()
            path = f"/sessions/{session['session_id']}"
            headers = {"Authorization": "Bearer " + session["token"]}
            payload = {"operation_id": str(uuid4()), "target": "llm", "mode": "unavailable"}
            assert (await client.post(path + "/faults", json=payload)).status_code == 404
            assert (
                await client.post(
                    path + "/faults", json=payload, headers={"Authorization": "Bearer wrong"}
                )
            ).status_code == 404
            response = await client.post(path + "/faults", headers=headers, json=payload)
            assert response.status_code == (200 if enabled else 404)
            if enabled:
                assert (
                    await client.post(
                        path + "/faults",
                        headers=headers,
                        json={"operation_id": str(uuid4()), "target": "llm", "mode": "timeout"},
                    )
                ).status_code == 422
                status = await client.get(path + "/status", headers=headers)
                assert status.json()["active_fault"]["target"] == "llm"
                assert status.json()["faults_enabled"]
                assert session["token"] not in status.text and "test-token" not in status.text
                assert (await client.delete(path + "/faults", headers=headers)).status_code == 200
                assert (await client.get(path + "/status", headers=headers)).json()[
                    "active_fault"
                ] is None
            assert (await client.delete(path, headers=headers)).status_code == 200


async def test_apply_timeout_retains_bound_input_and_recovers_one_score_without_audio(
    system: System,
) -> None:
    await transition_to_greeting(system)
    telemetry = Telemetry("voice-session")
    faults = Faults(telemetry)
    backend = Backend(system.conversation, "test-token", timeout=0.2)
    scoped = ObservedBackend(backend, telemetry, faults)
    text = "I confirm my masters degree."
    provider = ToolProvider(proposals(text)["proposals"])
    dialogue = QualifiedDialogue(
        scoped, LLMRouter((ProviderSlot(provider),)), system.conversation_id, system.call_id
    )
    events: list[dict[str, object]] = []

    async def notify(payload: dict[str, object]) -> None:
        events.append(payload)

    dialogue.notify = notify
    faults.arm(FaultCreate(operation_id=uuid4(), target="dependency", mode="timeout"))
    uid = uuid4()
    with pytest.raises(DependencyError):
        await dialogue.reply(text, uid)
    async with system.sessions() as db:
        message = await db.get(Message, uid)
        assert message is not None and message.turn_status == "PENDING"
        assert cast(dict[str, Any], message.message_metadata)["stage"] == "BOUND"
        assert (
            await db.scalar(
                select(func.count())
                .select_from(LeadScoreHistory)
                .where(LeadScoreHistory.lead_id == system.lead_id)
            )
            == 0
        )
    assert not events
    # The original shared backend is untouched; faults belong to the wrapper only.
    assert not hasattr(backend, "faults")
    faults.reset()
    await dialogue.recover()
    await dialogue.recover()
    assert len(events) == 1 and events[0]["type"] == "qualification"
    assert len(provider.requests) == 1  # Recovery reuses frozen facts, never fresh extraction.
    async with system.sessions() as db:
        assert (
            await db.scalar(
                select(func.count())
                .select_from(LeadScoreHistory)
                .where(LeadScoreHistory.lead_id == system.lead_id)
            )
            == 1
        )
        messages = list(
            await db.scalars(
                select(Message).where(Message.conversation_id == system.conversation_id)
            )
        )
        assert len(messages) == 1 and messages[0].turn_status == "APPLIED"
    encoded = json.dumps(telemetry.snapshot())
    assert "timeout" in encoded and text not in encoded


async def test_authenticated_domain_snapshots_gateway_correlation_and_outage(
    system: System,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = gateway_app(
        settings=GatewaySettings("test", "http://lead", "test-token", 2),
        client=LeadServiceClient(system.lead, service_auth_token="test-token"),
        conversation_client=ConversationServiceClient(
            system.conversation, service_auth_token="test-token"
        ),
    )
    for client in (system.lead, system.conversation):
        assert (await client.get("/v1/observability")).status_code == 401
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://gateway"
        ) as client:
            await client.get(
                f"/v1/conversations/{system.conversation_id}",
                headers={"X-Request-ID": "diagnostic-trace"},
            )
            response = await client.get("/v1/observability")
            assert response.status_code == 200
            body = response.json()
            assert body["lead"]["service"] == "lead-service"
            assert body["conversation"]["service"] == "conversation-service"
            assert "diagnostic-trace" in json.dumps(body["conversation"])
            assert "test-token" not in response.text

            async def unavailable(*args: Any, **kwargs: Any) -> Any:
                from api_gateway.client import LeadServiceUnavailableError

                raise LeadServiceUnavailableError("private")

            monkeypatch.setattr(app.state.lead_client, "request", unavailable)
            body = (await client.get("/v1/observability")).json()
            assert body["lead"] is None and body["conversation"] is not None
            assert "private" not in json.dumps(body)

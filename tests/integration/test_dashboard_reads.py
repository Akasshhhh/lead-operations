"""Dashboard discovery and durable read isolation across actual service boundaries."""

from typing import Any
from uuid import uuid4

import httpx
import pytest
from api_gateway.app import create_app as create_gateway_app
from api_gateway.client import ConversationServiceClient, LeadServiceClient
from api_gateway.settings import GatewaySettings
from conversation_service.client import LeadServiceUnavailableError
from sqlalchemy import select
from test_conversation_service import System, transition_to_greeting
from test_conversation_service import system as system
from voice_platform_db.models import DomainEvent
from voice_platform_runtime.app import create_app as create_voice_app
from voice_platform_runtime.backend import Backend

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
HEADERS = {"X-Service-Token": "test-token"}


async def test_discovery_call_event_reads_survive_lead_outage_and_preserve_scope(
    system: System,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await transition_to_greeting(system)

    async def unavailable(*args: Any, **kwargs: Any) -> Any:
        raise LeadServiceUnavailableError("private data")

    monkeypatch.setattr(system.conversation_app.state.lead_client, "get_qualification", unavailable)
    monkeypatch.setattr(system.conversation_app.state.lead_client, "get_lead", unavailable)
    path = f"/v1/conversations/{system.conversation_id}"
    assert (await system.conversation.get(path + "/live-state", headers=HEADERS)).status_code == 503
    discovered = await system.conversation.get(
        "/v1/conversations",
        headers=HEADERS,
        params={"lead_id": str(system.lead_id), "active_only": True, "limit": 1},
    )
    assert discovered.status_code == 200
    assert discovered.json()["items"][0]["id"] == str(system.conversation_id)
    assert discovered.json()["total"] == 1
    unrelated = await system.conversation.get(
        "/v1/conversations", headers=HEADERS, params={"lead_id": str(uuid4())}
    )
    assert unrelated.status_code == 200 and unrelated.json()["items"] == []
    calls = await system.conversation.get(path + "/calls", headers=HEADERS, params={"limit": 1})
    assert calls.status_code == 200
    assert calls.json()["active_call"]["id"] == str(system.call_id)
    assert calls.json()["items"][0]["status"] == "CONNECTED"
    events = await system.conversation.get(path + "/events", headers=HEADERS, params={"limit": 2})
    assert events.status_code == 200 and len(events.json()["items"]) == 2
    assert events.json()["total"] >= 6
    assert all(
        "payload" not in event and "request_hash" not in event and "last_error" not in event
        for event in events.json()["items"]
    )
    second = await system.conversation.get(
        path + "/events", headers=HEADERS, params={"limit": 2, "offset": 2}
    )
    assert not {e["event_id"] for e in events.json()["items"]} & {
        e["event_id"] for e in second.json()["items"]
    }
    assert (
        await system.conversation.get(f"/v1/conversations/{uuid4()}/events", headers=HEADERS)
    ).status_code == 404
    async with system.sessions() as db:
        actual = list(
            await db.scalars(
                select(DomainEvent).where(DomainEvent.aggregate_id == system.conversation_id)
            )
        )
        assert actual and all(event.published_at is None for event in actual)


@pytest.mark.parametrize(
    "suffix,params",
    [
        ("", {}),
        ("", {"lead_id": "not-a-uuid"}),
        ("/calls", {"limit": 101}),
        ("/calls", {"limit": 0}),
        ("/events", {"offset": -1}),
        ("/events", {"offset": 10001}),
    ],
)
async def test_dashboard_read_bounds_and_auth(
    system: System, suffix: str, params: dict[str, Any]
) -> None:
    path = "/v1/conversations" + (f"/{system.conversation_id}{suffix}" if suffix else "")
    assert (await system.conversation.get(path, headers=HEADERS, params=params)).status_code == 422
    assert (await system.conversation.get(path, params=params)).status_code == 401


async def test_gateway_discovery_and_event_reads_validate_contracts(system: System) -> None:
    app = create_gateway_app(
        settings=GatewaySettings("test", "http://lead", "test-token", 2),
        client=LeadServiceClient(system.lead, service_auth_token="test-token"),
        conversation_client=ConversationServiceClient(
            system.conversation, service_auth_token="test-token"
        ),
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://gateway"
        ) as client:
            response = await client.get(
                "/v1/conversations",
                params={"lead_id": str(system.lead_id)},
                headers={"X-Request-ID": "dashboard-read-test"},
            )
            assert (
                response.status_code == 200
                and response.headers["X-Request-ID"] == "dashboard-read-test"
            )
            for suffix in ("calls", "events"):
                response = await client.get(f"/v1/conversations/{system.conversation_id}/{suffix}")
                assert response.status_code == 200 and response.json()["total"] > 0


async def test_runtime_status_requires_capability_and_contains_no_secrets(system: System) -> None:
    app = create_voice_app("http://conversation", "test-token")
    async with app.router.lifespan_context(app):
        app.state.backend = Backend(system.conversation, "test-token")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://voice"
        ) as client:
            created = await client.post(
                "/sessions",
                json={
                    "conversation_id": str(system.conversation_id),
                    "call_id": str(system.call_id),
                    "manual_turns": True,
                },
            )
            assert created.status_code == 200, created.text
            body = created.json()
            path = f"/sessions/{body['session_id']}"
            assert (await client.get(path + "/status")).status_code == 404
            assert (
                await client.get(path + "/status", headers={"Authorization": "Bearer wrong"})
            ).status_code == 404
            headers = {"Authorization": "Bearer " + body["token"]}
            status = await client.get(path + "/status", headers=headers)
            assert status.status_code == 200
            assert status.json()["scope"] == "session_process_local"
            assert status.json()["conversation_id"] == str(system.conversation_id)
            assert status.json()["media_state"] == "unattached"
            assert (
                body["token"] not in status.text
                and "sdp" not in status.text
                and "test-token" not in status.text
            )
            assert len(status.json()["providers"]["llm"]) == 1
            assert status.json()["providers"]["llm"][0]["success_count"] == 0
            ended = await client.delete(path, headers=headers)
            assert ended.status_code == 200
            assert (await client.get(path + "/status", headers=headers)).status_code == 404

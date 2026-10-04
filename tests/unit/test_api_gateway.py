from __future__ import annotations

from typing import Any, cast
from uuid import uuid4

import httpx
import pytest
from api_gateway.app import create_app
from api_gateway.client import LeadServiceClient, LeadServiceUnavailableError
from api_gateway.settings import GatewayConfigurationError, GatewaySettings


def test_production_gateway_requires_internal_authentication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("LEAD_SERVICE_AUTH_TOKEN", raising=False)

    with pytest.raises(GatewayConfigurationError, match="LEAD_SERVICE_AUTH_TOKEN"):
        GatewaySettings.from_env()


@pytest.mark.asyncio
async def test_lead_client_sends_service_token_and_request_id() -> None:
    observed: dict[str, str] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        observed["service_token"] = request.headers["X-Service-Token"]
        observed["request_id"] = request.headers["X-Request-ID"]
        return httpx.Response(200, json={"status": "ok", "database": "ok"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://lead-service"
    ) as http_client:
        client = LeadServiceClient(http_client, service_auth_token="test-token")
        result = await client.health(request_id="request-123")

    assert result == {"status": "ok", "database": "ok"}
    assert observed == {"service_token": "test-token", "request_id": "request-123"}


class FakeLeadClient:
    async def list_leads(self, **_: Any) -> dict[str, object]:
        return {"items": [], "total": 0, "limit": 50, "offset": 0}

    async def create_lead(self, **_: Any) -> dict[str, object]:
        now = "2026-01-01T00:00:00Z"
        return {
            "id": str(uuid4()),
            "display_name": "Gateway Lead",
            "synthetic_profile_key": "gateway-lead",
            "intent": None,
            "target_country": "Canada",
            "preferred_language": "en",
            "status": "NEW",
            "created_at": now,
            "updated_at": now,
            "version": 1,
        }

    async def health(self, **_: Any) -> dict[str, str]:
        return {"status": "ok", "database": "ok"}


@pytest.mark.asyncio
async def test_gateway_validates_and_propagates_request_id() -> None:
    fake_client = cast(LeadServiceClient, FakeLeadClient())
    settings = GatewaySettings(
        environment="test",
        lead_service_url="http://unused",
        service_auth_token="test-token",
        request_timeout_seconds=1,
    )
    app = create_app(settings=settings, client=fake_client)

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://gateway") as client:
            response = await client.post(
                "/v1/leads",
                headers={"X-Request-ID": "gateway-request"},
                json={
                    "display_name": "Gateway Lead",
                    "synthetic_profile_key": "gateway-lead",
                    "target_country": "Canada",
                },
            )

    assert response.status_code == 201
    assert response.headers["X-Request-ID"] == "gateway-request"
    assert response.json()["synthetic_profile_key"] == "gateway-lead"


@pytest.mark.asyncio
async def test_gateway_returns_standard_downstream_unavailable_error() -> None:
    class UnavailableClient:
        async def list_leads(self, **_: Any) -> None:
            raise LeadServiceUnavailableError("down")

    settings = GatewaySettings(
        environment="test",
        lead_service_url="http://unused",
        service_auth_token=None,
        request_timeout_seconds=1,
    )
    app = create_app(settings=settings, client=cast(LeadServiceClient, UnavailableClient()))

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://gateway") as client:
            response = await client.get("/v1/leads")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "lead_service_unavailable"

import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from api_gateway.app import create_app
from api_gateway.client import LeadServiceClient, LeadServiceUnavailableError
from api_gateway.settings import GatewayConfigurationError, GatewaySettings
from lead_service.app import create_app as create_lead_app
from voice_platform_contracts.lead import LeadUpdate


def settings() -> GatewaySettings:
    return GatewaySettings("test", "http://lead", "test-token", 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [{"status": "CONTACTED"}, {"intent": None}])
async def test_gateway_preserves_patch_omission_and_explicit_null(
    changes: dict[str, object],
) -> None:
    body = changes | {"expected_version": 1}
    observed: list[dict[str, object]] = []

    def downstream(request: httpx.Request) -> httpx.Response:
        received = json.loads(request.content)
        LeadUpdate.model_validate(received)
        observed.append(received)
        return httpx.Response(404, json={"detail": "missing lead"})

    async with httpx.AsyncClient(
        base_url="http://lead", transport=httpx.MockTransport(downstream)
    ) as http:
        app = create_app(
            settings=settings(), client=LeadServiceClient(http, service_auth_token="test")
        )
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url="http://gateway"
            ) as client:
                response = await client.patch(f"/v1/leads/{uuid4()}", json=body)
    assert response.status_code == 404
    assert observed == [body]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectTimeout,
        httpx.ReadTimeout,
        httpx.WriteTimeout,
        httpx.PoolTimeout,
        httpx.ConnectError,
        httpx.ReadError,
        httpx.RemoteProtocolError,
    ],
)
async def test_transport_failures_return_controlled_503(error: type[httpx.RequestError]) -> None:
    def downstream(request: httpx.Request) -> httpx.Response:
        raise error("private transport details", request=request)

    async with httpx.AsyncClient(
        base_url="http://lead", transport=httpx.MockTransport(downstream)
    ) as http:
        app = create_app(
            settings=settings(), client=LeadServiceClient(http, service_auth_token="test")
        )
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url="http://gateway"
            ) as client:
                response = await client.get("/v1/leads")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "lead_service_unavailable"
    assert "private" not in response.text


@pytest.mark.parametrize("token", [None, "", "   ", "local-lead-service-token"])
def test_production_rejects_empty_and_compose_demo_tokens(
    token: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    if token is None:
        monkeypatch.delenv("LEAD_SERVICE_AUTH_TOKEN", raising=False)
    else:
        monkeypatch.setenv("LEAD_SERVICE_AUTH_TOKEN", token)
    with pytest.raises(GatewayConfigurationError, match="LEAD_SERVICE_AUTH_TOKEN"):
        GatewaySettings.from_env()
    with pytest.raises(ValueError, match="LEAD_SERVICE_AUTH_TOKEN"):
        create_lead_app()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status_code,body,expected",
    [
        (200, "not-json", 502),
        (200, "{}", 502),
        (204, "", 502),
        (302, "", 502),
        (500, "private traceback and credentials", 502),
        (401, "private token", 502),
        (409, '{"detail":"private profile key"}', 409),
        (503, '{"detail":"private database host"}', 503),
    ],
)
async def test_bad_upstream_responses_are_controlled_and_redacted(
    status_code: int,
    body: str,
    expected: int,
) -> None:
    async with httpx.AsyncClient(
        base_url="http://lead",
        transport=httpx.MockTransport(lambda _: httpx.Response(status_code, content=body)),
    ) as http:
        app = create_app(
            settings=settings(), client=LeadServiceClient(http, service_auth_token="test")
        )
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url="http://gateway"
            ) as client:
                response = await client.get("/v1/leads", headers={"X-Request-ID": "audit-response"})
    assert response.status_code == expected
    assert "private" not in response.text
    assert response.json()["error"]["request_id"] == "audit-response"
    assert response.headers["X-Request-ID"] == "audit-response"


@pytest.mark.asyncio
async def test_validation_redacts_input_and_replaces_oversized_request_id() -> None:
    app = create_app(settings=settings())
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://gateway"
        ) as client:
            response = await client.post(
                "/v1/leads", json={"private": "sensitive data"}, headers={"X-Request-ID": "x" * 121}
            )
            missing = await client.get("/not-a-route")
    assert response.status_code == 422
    assert "sensitive" not in response.text
    assert 1 <= len(response.headers["X-Request-ID"]) <= 120
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "http_error"


@pytest.mark.parametrize(
    "url", ["", "ftp://lead", "http://user:secret@lead", "http://lead?token=x", "http://lead:99999"]
)
def test_invalid_service_url_fails_at_startup(url: str) -> None:
    with pytest.raises(GatewayConfigurationError):
        GatewaySettings("test", url, None, 1)


def test_injected_production_settings_cannot_bypass_token_guard() -> None:
    with pytest.raises(GatewayConfigurationError, match="LEAD_SERVICE_AUTH_TOKEN"):
        GatewaySettings("production", "http://lead", "local-lead-service-token", 1)


@pytest.mark.asyncio
async def test_whole_request_deadline_bounds_a_slow_upstream() -> None:
    async def blocked(_: httpx.Request) -> httpx.Response:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async with httpx.AsyncClient(
        base_url="http://lead", transport=httpx.MockTransport(blocked)
    ) as http:
        client = LeadServiceClient(http, service_auth_token=None, timeout_seconds=0.01)
        with pytest.raises(LeadServiceUnavailableError):
            await client.health(request_id="deadline-test")

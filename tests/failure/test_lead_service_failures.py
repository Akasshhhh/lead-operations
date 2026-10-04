from __future__ import annotations

import socket
from uuid import uuid4

import httpx
import pytest
from lead_service.app import create_app


@pytest.mark.asyncio
@pytest.mark.failure
async def test_all_lead_endpoints_return_503_when_database_is_down() -> None:
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
        app = create_app(
            database_url=f"postgresql+asyncpg://voice_ai:voice_ai@127.0.0.1:{port}/voice_ai"
        )
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url="http://lead"
            ) as client:
                for method, path, body in [
                    ("GET", "/health", None),
                    ("GET", "/v1/leads", None),
                    ("GET", f"/v1/leads/{uuid4()}", None),
                    ("GET", f"/v1/leads/{uuid4()}/qualification", None),
                    (
                        "POST",
                        "/v1/leads",
                        {"display_name": "Lead", "synthetic_profile_key": "outage"},
                    ),
                    (
                        "PATCH",
                        f"/v1/leads/{uuid4()}",
                        {"status": "CONTACTED", "expected_version": 1},
                    ),
                ]:
                    response = await client.request(method, path, json=body)
                    assert response.status_code == 503, (method, path, response.text)
                    assert response.json() == {"detail": "database unavailable"}

from __future__ import annotations

import httpx
import pytest
from lead_service.app import create_app


@pytest.mark.asyncio
@pytest.mark.failure
async def test_lead_service_rejects_missing_internal_token() -> None:
    app = create_app(
        app_env="test",
        service_auth_token="expected-token",
        database_url="postgresql+asyncpg://unused:unused@127.0.0.1:59999/voice_ai",
    )

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://lead-service") as client:
            response = await client.get("/health")

    assert response.status_code == 401
    assert response.json() == {"detail": "invalid service credentials"}

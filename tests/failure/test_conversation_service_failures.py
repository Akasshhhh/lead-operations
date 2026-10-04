from __future__ import annotations

import httpx
import pytest
from conversation_service.app import create_app


@pytest.mark.asyncio
@pytest.mark.failure
async def test_conversation_health_returns_503_when_database_is_down() -> None:
    app = create_app(
        database_url="postgresql+asyncpg://voice_ai:voice_ai@127.0.0.1:59998/voice_ai",
        app_env="test",
        service_auth_token="test-token",
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://conversation"
        ) as client:
            response = await client.get("/health", headers={"X-Service-Token": "test-token"})
    assert response.status_code == 503
    assert response.json() == {"detail": "database unavailable"}

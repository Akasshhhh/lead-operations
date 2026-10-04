from __future__ import annotations

import os
from uuid import uuid4

import httpx
import pytest
from lead_service.app import create_app


@pytest.mark.asyncio
@pytest.mark.integration
async def test_lead_api_lifecycle_against_postgres() -> None:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is required for Lead Service API integration tests")

    app = create_app(database_url=database_url)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://lead-service") as client:
            health = await client.get("/health")
            assert health.status_code == 200
            assert health.json() == {"status": "ok", "database": "ok"}

            create_response = await client.post(
                "/v1/leads",
                json={
                    "display_name": "API Integration Lead",
                    "synthetic_profile_key": f"api-integration-{uuid4()}",
                    "target_country": "Canada",
                    "initial_answers": {"years_experience": 4},
                },
            )
            assert create_response.status_code == 201
            lead = create_response.json()

            detail = await client.get(f"/v1/leads/{lead['id']}")
            qualification = await client.get(f"/v1/leads/{lead['id']}/qualification")
            update = await client.patch(
                f"/v1/leads/{lead['id']}",
                json={"status": "CONTACTED", "expected_version": lead["version"]},
            )
            stale_update = await client.patch(
                f"/v1/leads/{lead['id']}",
                json={"status": "NEW", "expected_version": lead["version"]},
            )

            assert detail.status_code == 200
            assert qualification.status_code == 200
            assert qualification.json()["answers"][0]["field_key"] == "years_experience"
            assert update.status_code == 200
            assert update.json()["status"] == "CONTACTED"
            assert stale_update.status_code == 409

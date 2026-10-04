from __future__ import annotations

import os
from asyncio import gather
from collections.abc import AsyncGenerator
from uuid import uuid4

import pytest
import pytest_asyncio
from lead_service.schemas import LeadCreate, LeadUpdate
from lead_service.service import DuplicateLeadError, LeadService, LeadVersionConflictError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine
from voice_platform_db import create_async_engine, create_session_factory
from voice_platform_db.models import DomainEvent


@pytest_asyncio.fixture
async def database_engine() -> AsyncGenerator[AsyncEngine, None]:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is required for Lead Service integration tests")

    engine = create_async_engine(database_url)
    yield engine
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_lead_lifecycle_persists_profile_and_outbox_event(
    database_engine: AsyncEngine,
) -> None:
    session_factory = create_session_factory(database_engine)
    profile_key = f"integration-lead-{uuid4()}"

    async with session_factory() as session:
        service = LeadService(session)
        async with session.begin():
            lead = await service.create_lead(
                LeadCreate(
                    display_name="Integration Lead",
                    synthetic_profile_key=profile_key,
                    target_country="Canada",
                    initial_answers={"years_experience": 6},
                )
            )
            lead_id = lead.id
        async with session.begin():
            qualification = await service.get_qualification(lead_id)
        async with session.begin():
            updated = await service.update_lead(
                lead_id,
                LeadUpdate(status="CONTACTED", expected_version=1),
            )

        assert qualification.profile.lead_id == lead_id
        assert len(qualification.answers) == 1
        assert updated.status == "CONTACTED"
        assert updated.version == 2

        with pytest.raises(LeadVersionConflictError):
            async with session.begin():
                await service.update_lead(lead_id, LeadUpdate(status="NEW", expected_version=1))

        with pytest.raises(DuplicateLeadError):
            async with session.begin():
                await service.create_lead(
                    LeadCreate(display_name="Duplicate", synthetic_profile_key=profile_key)
                )

        async with session.begin():
            event = (
                await session.execute(
                    select(DomainEvent).where(
                        DomainEvent.aggregate_id == lead_id,
                        DomainEvent.event_type == "lead.created",
                    )
                )
            ).scalar_one()
            assert event.event_type == "lead.created"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_concurrent_updates_allow_only_one_expected_version(
    database_engine: AsyncEngine,
) -> None:
    session_factory = create_session_factory(database_engine)
    profile_key = f"concurrent-lead-{uuid4()}"

    async with session_factory.begin() as session:
        lead = await LeadService(session).create_lead(
            LeadCreate(display_name="Concurrent Lead", synthetic_profile_key=profile_key)
        )
    lead_id = lead.id
    expected_version = 1

    async def update(status: str) -> str:
        async with session_factory.begin() as session:
            try:
                updated = await LeadService(session).update_lead(
                    lead_id,
                    LeadUpdate(status=status, expected_version=expected_version),
                )
                return updated.status
            except LeadVersionConflictError:
                return "CONFLICT"

    results = await gather(update("CONTACTED"), update("QUALIFIED"))

    assert results.count("CONFLICT") == 1
    assert set(results) & {"CONTACTED", "QUALIFIED"}

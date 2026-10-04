from __future__ import annotations

import os
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine
from voice_platform_db import create_async_engine, create_session_factory
from voice_platform_db.models import Conversation, DomainEvent, Lead

EXPECTED_TABLES = {
    "conversation.calls",
    "conversation.conversation_summaries",
    "conversation.conversations",
    "conversation.messages",
    "conversation.transcript_segments",
    "evaluation.evaluation_results",
    "evaluation.evaluation_runs",
    "evaluation.scenarios",
    "lead.lead_score_history",
    "lead.lead_scores",
    "lead.leads",
    "lead.qualification_answers",
    "lead.qualification_profiles",
    "platform.audit_log",
    "platform.domain_events",
    "platform.fault_injections",
    "platform.processed_events",
    "platform.provider_health",
    "workflow.followup_attempts",
    "workflow.followups",
    "workflow.handoffs",
}


@pytest_asyncio.fixture
async def database_engine() -> AsyncIterator[AsyncEngine]:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is required for PostgreSQL integration tests")

    engine = create_async_engine(database_url)
    yield engine
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_migrated_schema_contains_all_approved_tables(database_engine: AsyncEngine) -> None:
    async with database_engine.connect() as connection:
        result = await connection.execute(
            text(
                "SELECT table_schema || '.' || table_name "
                "FROM information_schema.tables "
                "WHERE table_schema IN ("
                "'lead', 'conversation', 'workflow', 'evaluation', 'platform')"
            )
        )

    actual_tables = {row[0] for row in result}
    assert actual_tables == EXPECTED_TABLES


@pytest.mark.asyncio
@pytest.mark.integration
async def test_server_timestamp_defaults_are_dynamic(database_engine: AsyncEngine) -> None:
    async with database_engine.connect() as connection:
        defaults: list[str] = list(
            (
                await connection.execute(
                    text(
                        "SELECT column_default FROM information_schema.columns "
                        "WHERE table_schema IN "
                        "('lead','conversation','workflow','evaluation','platform') "
                        "AND data_type='timestamp with time zone' AND column_default IS NOT NULL"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(defaults) == 24
    assert set(defaults) == {"now()"}
    timestamps = []
    for _ in range(2):
        async with database_engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        "INSERT INTO platform.audit_log (id,action,actor,resource) "
                        "VALUES (:id,'timestamp-test','test','test') "
                        "RETURNING created_at, transaction_timestamp()"
                    ),
                    {"id": uuid4()},
                )
            ).one()
            assert row[0] == row[1]
            timestamps.append(row[0])
            await connection.rollback()
    assert timestamps[1] > timestamps[0]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_core_models_round_trip_and_event_idempotency_schema(
    database_engine: AsyncEngine,
) -> None:
    session_factory = create_session_factory(database_engine)
    async with session_factory() as session:
        async with session.begin():
            lead = Lead(
                display_name="Integration Lead",
                synthetic_profile_key=f"integration-{uuid4()}",
                target_country="Canada",
            )
            session.add(lead)
            await session.flush()

            conversation = Conversation(lead_id=lead.id)
            session.add(conversation)
            await session.flush()

            event = DomainEvent(
                event_type="integration.test",
                producer="integration-test",
                aggregate_type="lead",
                aggregate_id=lead.id,
                aggregate_version=1,
                idempotency_key=f"integration-event-{uuid4()}",
                payload={"lead_id": str(lead.id)},
            )
            session.add(event)
            await session.flush()

            assert lead.id is not None
            assert conversation.lead_id == lead.id
            assert event.aggregate_id == lead.id

            duplicate = DomainEvent(
                event_type="integration.test.duplicate",
                producer="integration-test",
                aggregate_type="lead",
                aggregate_id=lead.id,
                aggregate_version=2,
                idempotency_key=event.idempotency_key,
                payload={"lead_id": str(lead.id)},
            )
            with pytest.raises(IntegrityError):
                async with session.begin_nested():
                    session.add(duplicate)
                    await session.flush()

        async with session.begin():
            await session.delete(conversation)
            await session.delete(lead)

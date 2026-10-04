from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine
from voice_platform_db import create_async_engine, create_session_factory
from voice_platform_db.models import Conversation, Lead, Message, TranscriptSegment


@pytest_asyncio.fixture
async def retention_rows() -> AsyncGenerator[tuple[AsyncEngine, str, UUID, UUID], None]:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is required")
    engine = create_async_engine(database_url)
    sessions = create_session_factory(engine)
    lead_id = uuid4()
    conversation_id = uuid4()
    lead = Lead(
        id=lead_id,
        display_name="Retention CLI Lead",
        synthetic_profile_key=f"retention-cli-{uuid4().hex}",
    )
    conversation = Conversation(
        id=conversation_id,
        lead_id=lead_id,
        state="COMPLETED",
        completed_at=datetime.now(UTC) - timedelta(days=30),
    )
    message = Message(
        id=uuid4(),
        conversation_id=conversation_id,
        speaker="USER",
        text="CLI retention secret",
        sequence_number=1,
        turn_status="APPLIED",
    )
    segment = TranscriptSegment(
        conversation_id=conversation_id,
        speaker="USER",
        text="CLI retention secret",
        sequence_number=1,
        provider_segment_id=str(uuid4()),
    )
    async with sessions.begin() as session:
        session.add_all([lead, conversation, message, segment])
        await session.flush()
        identifiers = (lead_id, conversation_id)
    try:
        yield engine, database_url, *identifiers
    finally:
        async with sessions.begin() as session:
            await session.execute(delete(Conversation).where(Conversation.id == identifiers[1]))
            await session.execute(delete(Lead).where(Lead.id == identifiers[0]))
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_retention_cli_supports_dry_run_and_idempotent_redaction(
    retention_rows: tuple[AsyncEngine, str, UUID, UUID],
) -> None:
    engine, database_url, _, conversation_id = retention_rows
    root = Path(__file__).resolve().parents[2]
    env = os.environ | {
        "DATABASE_URL": database_url,
        "PYTHONPATH": os.pathsep.join(
            str(root / path)
            for path in (
                "packages/configuration",
                "packages/database",
                "packages/contracts",
                "services/conversation-service/src",
            )
        ),
    }
    command = [
        sys.executable,
        "-m",
        "conversation_service",
        "retention",
        "--before",
        (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        "--reason",
        "cli-test",
    ]
    dry_run = subprocess.run(
        [*command, "--dry-run"], env=env, capture_output=True, text=True, check=True, timeout=30
    )
    assert json.loads(dry_run.stdout) == {
        "dry_run": True,
        "messages_redacted": 1,
        "segments_redacted": 1,
    }
    redacted = subprocess.run(
        command, env=env, capture_output=True, text=True, check=True, timeout=30
    )
    assert json.loads(redacted.stdout) == {
        "dry_run": False,
        "messages_redacted": 1,
        "segments_redacted": 1,
    }
    repeated = subprocess.run(
        [*command, "--dry-run"], env=env, capture_output=True, text=True, check=True, timeout=30
    )
    assert json.loads(repeated.stdout) == {
        "dry_run": True,
        "messages_redacted": 0,
        "segments_redacted": 0,
    }
    sessions = create_session_factory(engine)
    async with sessions() as session:
        row = await session.scalar(
            select(Message).where(Message.conversation_id == conversation_id)
        )
        assert row is not None and row.text == "[REDACTED]"

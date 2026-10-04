"""Seed entrypoint with PostgreSQL-only settings and a real database."""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.engine import make_url
from voice_platform_db import create_async_engine, create_session_factory
from voice_platform_db.models import DomainEvent, Lead


@pytest.mark.integration
@pytest.mark.asyncio
async def test_seed_cli_with_postgres_fields_is_idempotent(tmp_path: Path) -> None:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL required")
    url = make_url(database_url)
    key = f"seed-audit-{uuid4().hex}"
    fixture = tmp_path / "leads.json"
    fixture.write_text(json.dumps([{"display_name": "Seed CLI", "synthetic_profile_key": key}]))
    env = {
        k: v
        for k, v in os.environ.items()
        if k
        not in {
            "DATABASE_URL",
            "REDIS_HOST",
            "REDIS_PORT",
            "REDIS_URL",
            "APP_ENV",
        }
    }
    env.update(
        POSTGRES_HOST=url.host or "localhost",
        POSTGRES_PORT=str(url.port or 5432),
        POSTGRES_USER=url.username or "",
        POSTGRES_PASSWORD=url.password or "",
        POSTGRES_DB=url.database or "",
        LEAD_SEED_FILE=str(fixture),
    )
    # Match the documented `make seed-leads` host launcher and image import paths.
    root = Path(__file__).resolve().parents[2]
    env["PYTHONPATH"] = os.pathsep.join(
        str(root / path)
        for path in (
            "services/lead-service/src",
            "packages/configuration",
            "packages/database",
            "packages/contracts",
        )
    )
    engine = create_async_engine(database_url)
    sessions = create_session_factory(engine)
    lead_id = uuid5(NAMESPACE_URL, f"voice-ai-platform:seed-lead:{key}")
    try:
        for expected in ("inserted=1 skipped=0", "inserted=0 skipped=1"):
            result = await asyncio.to_thread(
                subprocess.run,
                [sys.executable, "-m", "lead_service.seed"],
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert result.returncode == 0, result.stderr
            assert expected in result.stdout
        async with sessions() as session:
            lead = await session.get(Lead, lead_id)
            assert lead is not None and lead.version == 1
            count = await session.scalar(
                select(func.count())
                .select_from(DomainEvent)
                .where(DomainEvent.aggregate_id == lead_id)
            )
            assert count == 1
    finally:
        async with sessions.begin() as session:
            await session.execute(delete(DomainEvent).where(DomainEvent.aggregate_id == lead_id))
            await session.execute(delete(Lead).where(Lead.id == lead_id))
        await engine.dispose()

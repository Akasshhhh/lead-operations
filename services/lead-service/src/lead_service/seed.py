"""Deterministic, idempotent synthetic lead seeding command."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from pydantic import TypeAdapter
from voice_platform_config.settings import database_url_from_env
from voice_platform_db import create_async_engine, create_session_factory

from .schemas import LeadCreate
from .service import DuplicateLeadError, LeadService

DEFAULT_FIXTURE = Path("infrastructure/postgres/seed/leads.json")


def load_seed_file(path: Path) -> list[LeadCreate]:
    """Load and validate the deterministic lead fixture."""

    raw_data = json.loads(path.read_text(encoding="utf-8"))
    leads = TypeAdapter(list[LeadCreate]).validate_python(raw_data)
    keys = [lead.synthetic_profile_key for lead in leads]
    if len(keys) != len(set(keys)):
        raise ValueError("seed fixture contains duplicate synthetic profile keys")
    return leads


async def seed_leads(database_url: str, leads: list[LeadCreate]) -> tuple[int, int]:
    """Insert missing leads and return ``(inserted, skipped)`` counts."""

    engine = create_async_engine(database_url)
    session_factory = create_session_factory(engine)
    inserted = 0
    skipped = 0

    try:
        for lead_data in leads:
            deterministic_id = uuid5(
                NAMESPACE_URL, f"voice-ai-platform:seed-lead:{lead_data.synthetic_profile_key}"
            )
            try:
                async with session_factory.begin() as session:
                    await LeadService(session).create_lead(lead_data, lead_id=deterministic_id)
                inserted += 1
            except DuplicateLeadError:
                skipped += 1
    finally:
        await engine.dispose()

    return inserted, skipped


async def run_seed() -> None:
    database_url = database_url_from_env()
    fixture_path = Path(os.getenv("LEAD_SEED_FILE", str(DEFAULT_FIXTURE)))
    leads = load_seed_file(fixture_path)
    inserted, skipped = await seed_leads(database_url, leads)
    print(f"seeded leads: inserted={inserted} skipped={skipped}")


if __name__ == "__main__":
    asyncio.run(run_seed())

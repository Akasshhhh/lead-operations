"""Migration reversibility and metadata/default drift on an isolated database."""

import asyncio
import os
import subprocess
import sys
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine


@pytest.mark.integration
def test_migrations_preserve_data_and_round_trip_without_drift() -> None:
    admin_url = os.getenv("TEST_DATABASE_URL")
    if not admin_url:
        pytest.skip("TEST_DATABASE_URL required")
    name = f"migration_audit_{uuid4().hex}"
    url = make_url(admin_url).set(database=name).render_as_string(hide_password=False)

    async def sql(database_url: str, statement: str, *, autocommit: bool = False) -> object:
        engine = create_async_engine(
            database_url, isolation_level="AUTOCOMMIT" if autocommit else "READ COMMITTED"
        )
        try:
            async with engine.begin() as connection:
                result = await connection.execute(text(statement))
                return result.scalar() if result.returns_rows else None
        finally:
            await engine.dispose()

    def migrate(*args: str) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            env=os.environ | {"DATABASE_URL": url},
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    asyncio.run(sql(admin_url, f'CREATE DATABASE "{name}"', autocommit=True))
    try:
        migrate("upgrade", "6b30f517c820")
        original = asyncio.run(
            sql(
                url,
                "INSERT INTO platform.audit_log(id,action,actor,resource) "
                "VALUES (gen_random_uuid(),'audit','test','test') RETURNING created_at",
            )
        )
        migrate("upgrade", "e8f2a6b3c901")
        # Existing committed qualification has trusted call identity in its admitted turn.
        expected_call = asyncio.run(
            sql(
                url,
                """
            WITH new_lead AS (
                INSERT INTO lead.leads(id,display_name,synthetic_profile_key,preferred_language)
                VALUES(gen_random_uuid(),'Migration lead','migration-lead','en') RETURNING id
            ), profile AS (
                INSERT INTO lead.qualification_profiles(id,lead_id)
                SELECT gen_random_uuid(),id FROM new_lead RETURNING id
            ), conversation AS (
                INSERT INTO conversation.conversations(id,lead_id)
                SELECT gen_random_uuid(),id FROM new_lead RETURNING id
            ), call AS (
                INSERT INTO conversation.calls(id,conversation_id)
                SELECT gen_random_uuid(),id FROM conversation RETURNING id,conversation_id
            ), message AS (
                INSERT INTO conversation.messages(
                    id,conversation_id,call_id,speaker,text,sequence_number,metadata,turn_status
                ) SELECT gen_random_uuid(),conversation_id,id,'USER',
                    'I confirm 3 years of experience.',1,
                    jsonb_build_object('qualification_facts', jsonb_build_array(
                        jsonb_build_object('field_key','years_experience','value',3,
                            'status','CONFIRMED'))),
                    'APPLIED' FROM call RETURNING id,conversation_id,call_id
            ), receipt AS (
                INSERT INTO platform.domain_events(
                    event_id,event_type,producer,aggregate_type,aggregate_id,
                    aggregate_version,causation_id,payload
                ) SELECT gen_random_uuid(),'qualification.updated','lead-service','qualification',
                    profile.id,2,message.id,'{"request_hash":"historical-immutable"}'
                    FROM profile,message RETURNING event_id
            ), answer AS (
                INSERT INTO lead.qualification_answers(
                    id,qualification_profile_id,field_key,value,answer_status,source,conversation_id
                ) SELECT gen_random_uuid(),profile.id,'years_experience','3','CONFIRMED',
                    'CONVERSATION',message.conversation_id FROM profile,message RETURNING id
            ) SELECT call_id FROM message
        """,
            )
        )
        migrate("upgrade", "head")
        assert (
            asyncio.run(sql(url, "SELECT call_id FROM lead.qualification_answers")) == expected_call
        )
        assert asyncio.run(sql(url, "SELECT value FROM lead.qualification_answers")) == 3
        assert (
            asyncio.run(sql(url, "SELECT payload->>'request_hash' FROM platform.domain_events"))
            == "historical-immutable"
        )
        assert asyncio.run(sql(url, "SELECT created_at FROM platform.audit_log")) == original
        migrate("check")
        migrate("downgrade", "6b30f517c820")
        assert asyncio.run(sql(url, "SELECT created_at FROM platform.audit_log")) == original
        migrate("upgrade", "head")
        migrate("check")
        migrate("downgrade", "base")
        migrate("upgrade", "head")
        migrate("check")
    finally:
        asyncio.run(sql(admin_url, f'DROP DATABASE "{name}" WITH (FORCE)', autocommit=True))

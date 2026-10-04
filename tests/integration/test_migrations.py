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
        migrate("upgrade", "head")
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

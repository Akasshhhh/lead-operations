"""Async SQLAlchemy engine and session factory helpers."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)
from sqlalchemy.ext.asyncio import (
    create_async_engine as sqlalchemy_create_async_engine,
)


def create_async_engine(database_url: str, *, echo: bool = False) -> AsyncEngine:
    """Create a pre-pinged async engine for a service process."""

    return sqlalchemy_create_async_engine(
        database_url,
        echo=echo,
        pool_pre_ping=True,
        hide_parameters=True,
        pool_timeout=2,
        connect_args={"timeout": 2, "command_timeout": 3},
    )


def create_session_factory(
    engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    """Create the default async session factory for an engine."""

    return async_sessionmaker(engine, expire_on_commit=False)

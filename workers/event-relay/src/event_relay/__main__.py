"""Run with python -m event_relay [run|inspect|retry-event|replay-dlq]."""

import argparse
import asyncio
import json
import logging
import os
import signal
from uuid import UUID

from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine
from voice_platform_config.settings import database_url_from_env
from voice_platform_db import create_session_factory
from voice_platform_events.contracts import BusUnavailable
from voice_platform_events.logging import emit
from voice_platform_events.redis_bus import RedisEventBus
from voice_platform_events.relay import OutboxRelay
from voice_platform_events.retry import RetryPolicy


class Settings(BaseModel):
    database_url: str = Field(min_length=1, repr=False)
    redis_url: str = Field(min_length=1, repr=False)
    max_attempts: int = Field(default=5, ge=1, le=20)
    poll_seconds: float = Field(default=0.5, gt=0, le=30)

    @classmethod
    def from_env(cls) -> "Settings":
        return cls.model_validate(
            {
                "database_url": database_url_from_env(),
                "redis_url": os.getenv("REDIS_URL", ""),
                "max_attempts": os.getenv("EVENT_MAX_ATTEMPTS", "5"),
                "poll_seconds": os.getenv("EVENT_POLL_SECONDS", "0.5"),
            }
        )


async def main(args: argparse.Namespace) -> None:
    settings = Settings.from_env()
    # Separate worker pools and timeouts bound held outbox row locks during outages.
    engine = create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        hide_parameters=True,
        pool_size=2,
        max_overflow=0,
        pool_timeout=5,
        connect_args={"timeout": 5, "command_timeout": 5},
    )
    redis = Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=2,
        max_connections=4,
    )
    bus = RedisEventBus(redis)
    relay = OutboxRelay(
        create_session_factory(engine), bus, RetryPolicy(max_attempts=settings.max_attempts)
    )
    try:
        if args.command == "inspect":
            print(json.dumps({"outbox": await relay.inspect(), "redis": await bus.inspect()}))
        elif args.command == "retry-event":
            await relay.retry_event(UUID(args.event_id))
        elif args.command == "replay-dlq":
            print(await bus.replay_dead_letter(args.stream_id))
        else:
            stop = asyncio.Event()
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, stop.set)
            emit("relay.started")
            while not stop.is_set():
                try:
                    worked = await relay.run_once()
                except (SQLAlchemyError, OSError, TimeoutError, BusUnavailable) as exc:
                    emit("relay.dependency_failed", error=type(exc).__name__)
                    worked = False
                if not worked:
                    # Interruptible poll wait, not a business workflow scheduler.
                    try:
                        await asyncio.wait_for(stop.wait(), settings.poll_seconds)
                    except TimeoutError:
                        pass
            emit("relay.stopped")
    finally:
        await redis.aclose()
        await engine.dispose()


def cli() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("run")
    commands.add_parser("inspect")
    commands.add_parser("retry-event").add_argument("event_id", type=str)
    commands.add_parser("replay-dlq").add_argument("stream_id", type=str)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    asyncio.run(main(parser.parse_args()))


if __name__ == "__main__":
    cli()

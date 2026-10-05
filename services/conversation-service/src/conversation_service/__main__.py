"""Conversation Service operator commands."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime

from voice_platform_config.settings import database_url_from_env
from voice_platform_db import create_async_engine, create_session_factory

from .service import ConversationService
from .workflow import dispatch_followups


def parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("--before must include a timezone")
    return parsed


async def retention(args: argparse.Namespace) -> None:
    engine = create_async_engine(database_url_from_env())
    sessions = create_session_factory(engine)
    total_messages = 0
    total_segments = 0
    try:
        while True:
            async with sessions.begin() as session:
                result = await ConversationService(session).redact_expired_content(
                    cutoff=parse_timestamp(args.before),
                    batch_size=args.batch_size,
                    reason=args.reason,
                    dry_run=args.dry_run,
                )
            total_messages += result.messages_redacted
            total_segments += result.segments_redacted
            if args.dry_run or (result.messages_redacted == 0 and result.segments_redacted == 0):
                break
        print(
            json.dumps(
                {
                    "dry_run": args.dry_run,
                    "messages_redacted": total_messages,
                    "segments_redacted": total_segments,
                }
            )
        )
    finally:
        await engine.dispose()


async def followups(args: argparse.Namespace) -> None:
    engine = create_async_engine(database_url_from_env())
    try:
        result = await dispatch_followups(
            create_session_factory(engine), batch_size=args.batch_size
        )
        print(result.model_dump_json())
    finally:
        await engine.dispose()


def cli() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("retention")
    command.add_argument("--before", required=True, help="ISO-8601 cutoff including timezone")
    command.add_argument("--reason", default="retention")
    command.add_argument("--batch-size", type=int, default=500)
    command.add_argument("--dry-run", action="store_true")
    command = commands.add_parser(
        "dispatch-follow-ups", help="One bounded pass of due demo reminders"
    )
    command.add_argument("--batch-size", type=int, default=50)
    args = parser.parse_args()
    if args.command == "retention":
        asyncio.run(retention(args))
    elif args.command == "dispatch-follow-ups":
        asyncio.run(followups(args))


if __name__ == "__main__":
    cli()

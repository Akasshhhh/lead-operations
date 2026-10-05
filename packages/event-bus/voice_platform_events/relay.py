"""Transactional outbox relay. Publication may duplicate; it must never disappear."""

import time
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased
from voice_platform_contracts.events import EventEnvelope
from voice_platform_db.models import DomainEvent

from .contracts import BusUnavailable, EventBus
from .logging import emit
from .retry import RetryPolicy


class OutboxRelay:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        bus: EventBus,
        retry: RetryPolicy | None = None,
    ) -> None:
        self.sessions = sessions
        self.bus = bus
        self.retry = retry or RetryPolicy()

    async def run_once(self) -> bool:
        """Lock one aggregate head; commit publication status only after XADD.

        A lower unpublished version blocks later versions, including when it
        is locked, delayed, or exhausted. Different aggregates remain independent.
        """
        earlier = aliased(DomainEvent)
        started = time.monotonic()
        now = datetime.now(UTC)
        async with self.sessions.begin() as session:
            statement = (
                select(DomainEvent)
                .where(
                    DomainEvent.published_at.is_(None),
                    DomainEvent.publish_attempts < self.retry.max_attempts,
                    or_(DomainEvent.next_attempt_at.is_(None), DomainEvent.next_attempt_at <= now),
                    ~exists().where(
                        earlier.aggregate_type == DomainEvent.aggregate_type,
                        earlier.aggregate_id == DomainEvent.aggregate_id,
                        earlier.aggregate_version < DomainEvent.aggregate_version,
                        earlier.published_at.is_(None),
                    ),
                )
                .order_by(DomainEvent.occurred_at, DomainEvent.event_id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            row = (await session.execute(statement)).scalar_one_or_none()
            if row is None:
                return False
            row.publish_attempts += 1
            try:
                event = EventEnvelope.model_validate(row)
                stream_id = await self.bus.publish(event)
            except (ValidationError, ValueError) as exc:
                row.publish_attempts = self.retry.max_attempts
                row.last_error = type(exc).__name__
                row.next_attempt_at = None
                emit(
                    "outbox.exhausted",
                    event_id=str(row.event_id),
                    error=row.last_error,
                    request_id=row.request_id,
                    trace_id=row.trace_id,
                )
            except BusUnavailable as exc:
                row.last_error = type(exc.__cause__ or exc).__name__
                row.next_attempt_at = datetime.now(UTC) + timedelta(
                    seconds=self.retry.delay(row.publish_attempts, str(row.event_id))
                )
                emit(
                    "outbox.publish_failed",
                    event_id=str(row.event_id),
                    error=row.last_error,
                    attempt=row.publish_attempts,
                    exhausted=row.publish_attempts >= self.retry.max_attempts,
                    request_id=row.request_id,
                    trace_id=row.trace_id,
                )
            else:
                row.published_at = datetime.now(UTC)
                row.next_attempt_at = None
                row.last_error = None
        # A publish log must not imply a committed outbox mark before COMMIT succeeds.
        if row.published_at is not None:
            emit(
                "outbox.published",
                event_id=str(row.event_id),
                stream_id=stream_id,
                request_id=row.request_id,
                trace_id=row.trace_id,
                duration_ms=round((time.monotonic() - started) * 1000, 2),
            )
        return True

    async def retry_event(self, event_id: UUID) -> None:
        """Explicit replay supports both exhausted events and Redis data loss."""
        async with self.sessions.begin() as session:
            row = await session.get(DomainEvent, event_id, with_for_update=True)
            if row is None:
                raise ValueError("outbox event not found")
            row.published_at = None
            row.publish_attempts = 0
            row.next_attempt_at = None
            row.last_error = None
        emit("outbox.replay_requested", event_id=str(event_id))

    async def inspect(self) -> dict[str, int]:
        async with self.sessions() as session:
            pending = await session.scalar(
                select(func.count())
                .select_from(DomainEvent)
                .where(DomainEvent.published_at.is_(None))
            )
            exhausted = await session.scalar(
                select(func.count())
                .select_from(DomainEvent)
                .where(
                    DomainEvent.published_at.is_(None),
                    DomainEvent.publish_attempts >= self.retry.max_attempts,
                )
            )
        return {"unpublished": int(pending or 0), "exhausted": int(exhausted or 0)}

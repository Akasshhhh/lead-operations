"""Consumer runner: database effects and the deduplication marker commit together."""

import asyncio
import math
from collections.abc import Awaitable, Callable

from pydantic import ValidationError
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from voice_platform_contracts.events import EventEnvelope
from voice_platform_db.models import ProcessedEvent

from .contracts import EventBus
from .logging import emit

Handler = Callable[[EventEnvelope, AsyncSession], Awaitable[None]]


class Consumer:
    def __init__(
        self,
        bus: EventBus,
        sessions: async_sessionmaker[AsyncSession],
        *,
        group: str,
        name: str,
        handler: Handler,
        max_attempts: int = 5,
        timeout_seconds: float = 10,
    ) -> None:
        if (
            not 1 <= len(group) <= 120
            or not name
            or not 1 <= max_attempts <= 20
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("invalid consumer configuration")
        self.bus = bus
        self.sessions = sessions
        self.group = group
        self.name = name
        self.handler = handler
        self.max_attempts = max_attempts
        self.timeout_seconds = timeout_seconds

    async def run_once(self) -> bool:
        delivery = await self.bus.subscribe(self.group, self.name)
        if delivery is None:
            return False
        try:
            body = delivery.body.encode() if isinstance(delivery.body, str) else delivery.body
            if len(body) > 65_536:
                raise ValueError("oversized event")
            event = EventEnvelope.model_validate_json(body)
        except (ValidationError, ValueError):
            await self.bus.dead_letter(delivery, "invalid_envelope")
            emit("consumer.dead_letter", group=self.group, stream_id=delivery.stream_id)
            return True
        try:
            async with asyncio.timeout(self.timeout_seconds):
                async with self.sessions.begin() as session:
                    inserted = await session.scalar(
                        insert(ProcessedEvent)
                        .values(event_id=event.event_id, consumer_group=self.group)
                        .on_conflict_do_nothing()
                        .returning(ProcessedEvent.event_id)
                    )
                    # Check the marker even after the attempt budget: an earlier
                    # worker may have committed and crashed before acknowledgement.
                    if inserted is not None:
                        if delivery.attempt > self.max_attempts:
                            raise AttemptsExhausted
                        await self.handler(event, session)
        except Exception as exc:
            if delivery.attempt >= self.max_attempts:
                await self.bus.dead_letter(delivery, type(exc).__name__)
            emit(
                "consumer.failed",
                event_id=str(event.event_id),
                group=self.group,
                attempt=delivery.attempt,
                error=type(exc).__name__,
            )
            return True
        # Acknowledgement failure is not a handler failure. Propagate; the pending
        # delivery will be reclaimed and its committed marker will suppress effects.
        await self.bus.acknowledge(delivery)
        emit("consumer.acknowledged", event_id=str(event.event_id), group=self.group)
        return True


class AttemptsExhausted(RuntimeError):
    """Prior deliveries crashed without completing the database transaction."""

"""Transport boundary: domain code never depends on Redis response types."""

from dataclasses import dataclass
from typing import Protocol

from voice_platform_contracts.events import EventEnvelope


class BusUnavailable(RuntimeError):
    """The transport operation failed; delivery outcome may be ambiguous."""


@dataclass(frozen=True)
class Delivery:
    stream_id: str
    group: str
    consumer: str
    body: str | bytes
    attempt: int


class EventBus(Protocol):
    async def publish(self, event: EventEnvelope) -> str: ...

    async def subscribe(self, group: str, consumer: str) -> Delivery | None: ...

    async def acknowledge(self, delivery: Delivery) -> None: ...

    async def dead_letter(self, delivery: Delivery, reason: str) -> None: ...

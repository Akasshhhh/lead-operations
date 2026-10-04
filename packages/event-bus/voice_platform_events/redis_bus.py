"""Redis Streams transport with leased pending delivery and atomic dead letters."""

from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, cast

from redis.asyncio import Redis
from redis.client import NEVER_DECODE
from redis.exceptions import RedisError, ResponseError
from voice_platform_contracts.events import EventEnvelope

from .contracts import BusUnavailable, Delivery
from .retry import RetryPolicy

# A stale worker may finish after its lease is reclaimed. Only the current owner
# may acknowledge or dead-letter. A Redis script makes the ownership check atomic.
ACK = """
local pending = redis.call('XPENDING', KEYS[1], ARGV[1], ARGV[2], ARGV[2], 1)
if #pending == 0 or pending[1][2] ~= ARGV[3] then return 0 end
return redis.call('XACK', KEYS[1], ARGV[1], ARGV[2])
"""
DEAD_LETTER = """
local pending = redis.call('XPENDING', KEYS[1], ARGV[1], ARGV[2], ARGV[2], 1)
if #pending == 0 or pending[1][2] ~= ARGV[3] then return 0 end
redis.call('XADD', KEYS[2], '*', 'source_id', ARGV[2], 'group', ARGV[1],
    'body', ARGV[4], 'attempt', ARGV[5], 'reason', ARGV[6], 'failed_at', ARGV[7])
return redis.call('XACK', KEYS[1], ARGV[1], ARGV[2])
"""
REPLAY = """
local previous = redis.call('HGET', KEYS[3], ARGV[1])
if previous then return previous end
local entries = redis.call('XRANGE', KEYS[2], ARGV[1], ARGV[1])
if #entries == 0 then return false end
local fields = entries[1][2]
local body = nil
for i = 1, #fields, 2 do
    if fields[i] == 'body' then body = fields[i+1] end
end
if not body then return false end
local id = redis.call('XADD', KEYS[1], '*', 'body', body)
redis.call('HSET', KEYS[3], ARGV[1], id)
return id
"""


@contextmanager
def transport_errors() -> Iterator[None]:
    try:
        yield
    except RedisError as exc:
        raise BusUnavailable(type(exc).__name__) from exc


class RedisEventBus:
    def __init__(
        self,
        redis: Redis,
        *,
        stream: str = "events.domain",
        retry: RetryPolicy | None = None,
        visibility_ms: int = 30_000,
    ) -> None:
        if visibility_ms < 1:
            raise ValueError("visibility_ms must be positive")
        if not redis.connection_pool.connection_kwargs.get("decode_responses"):
            raise ValueError("RedisEventBus requires decode_responses=True")
        self.redis = redis
        # redis-py leaves execute_command untyped; keep its dynamic reply boundary here.
        self._execute = cast(Callable[..., Awaitable[Any]], redis.execute_command)
        self.stream = stream
        self.dlq = f"{stream}.dead-letter"
        self.retry = retry or RetryPolicy()
        self.visibility_ms = visibility_ms
        self._pending_cursor: dict[str, str] = {}

    async def publish(self, event: EventEnvelope) -> str:
        body = event.model_dump_json()
        if len(body.encode()) > 65_536:
            raise ValueError("event envelope exceeds 64 KiB")
        with transport_errors():
            return str(await self.redis.xadd(self.stream, {"body": body}))

    async def subscribe(self, group: str, consumer: str) -> Delivery | None:
        """One delivery per call. Retry due times live in Redis PEL idle timestamps.

        XCLAIM is the visibility-checked equivalent of XAUTOCLAIM, allowing a
        distinct exponential backoff for each entry's delivery count. Pages
        rotate so an old delayed entry cannot hide other abandoned messages.
        """
        with transport_errors():
            try:
                await self.redis.xgroup_create(self.stream, group, id="0-0", mkstream=True)
            except ResponseError as exc:
                if "BUSYGROUP" not in str(exc):
                    raise
            pending = await self.redis.xpending_range(
                self.stream, group, self._pending_cursor.get(group, "-"), "+", 100
            )
            self._pending_cursor[group] = (
                f"({pending[-1]['message_id']}" if len(pending) == 100 else "-"
            )
            for entry in pending:
                attempt = int(entry["times_delivered"])
                message_id = str(entry["message_id"])
                due_idle = self.visibility_ms + int(self.retry.delay(attempt, message_id) * 1000)
                if int(entry["time_since_delivered"]) < due_idle:
                    continue
                # Preserve malformed UTF-8 for quarantine instead of failing Redis decoding.
                claimed = await self._execute(
                    "XCLAIM",
                    self.stream,
                    group,
                    consumer,
                    due_idle,
                    message_id,
                    **{NEVER_DECODE: True},
                )
                if claimed:
                    _, fields = claimed[0]
                    return Delivery(
                        message_id, group, consumer, fields.get(b"body", b""), attempt + 1
                    )
            entries = await self._execute(
                "XREADGROUP",
                "GROUP",
                group,
                consumer,
                "COUNT",
                1,
                "STREAMS",
                self.stream,
                ">",
                **{NEVER_DECODE: True},
            )
            if not entries:
                return None
            message_id, fields = entries[0][1][0]
            return Delivery(
                message_id.decode("ascii"), group, consumer, fields.get(b"body", b""), 1
            )

    async def acknowledge(self, delivery: Delivery) -> None:
        with transport_errors():
            await self._eval(
                ACK, 1, self.stream, delivery.group, delivery.stream_id, delivery.consumer
            )

    async def dead_letter(self, delivery: Delivery, reason: str) -> None:
        with transport_errors():
            await self._eval(
                DEAD_LETTER,
                2,
                self.stream,
                self.dlq,
                delivery.group,
                delivery.stream_id,
                delivery.consumer,
                delivery.body,
                str(delivery.attempt),
                reason,
                datetime.now(UTC).isoformat(),
            )

    async def replay_dead_letter(self, stream_id: str) -> str:
        """Replay once per DLQ entry; successful groups deduplicate by event_id."""
        with transport_errors():
            result = await self._eval(
                REPLAY, 3, self.stream, self.dlq, f"{self.dlq}.replayed", stream_id
            )
        if result is None:
            raise ValueError("dead-letter entry not found")
        return str(result)

    async def _eval(self, script: str, numkeys: int, *args: str | bytes) -> object:
        return cast(object, await self._execute("EVAL", script, numkeys, *args))

    async def inspect(self) -> dict[str, object]:
        with transport_errors():
            exists = await self.redis.exists(self.stream)
            return {
                "stream": self.stream,
                "length": await self.redis.xlen(self.stream),
                "dead_letters": await self.redis.xlen(self.dlq),
                "groups": await self.redis.xinfo_groups(self.stream) if exists else [],
            }

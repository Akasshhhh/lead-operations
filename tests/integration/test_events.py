"""Real PostgreSQL and Redis; disposable database and unique stream per test."""

import asyncio
import os
import socket
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from lead_service.schemas import LeadCreate
from lead_service.service import LeadService
from redis.asyncio import Redis
from sqlalchemy import delete, event, func, select, text, update
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from voice_platform_contracts.events import EventEnvelope
from voice_platform_db import create_session_factory
from voice_platform_db.models import AuditLog, DomainEvent, Lead, ProcessedEvent
from voice_platform_events.consumer import Consumer
from voice_platform_events.contracts import BusUnavailable, Delivery
from voice_platform_events.redis_bus import RedisEventBus
from voice_platform_events.relay import OutboxRelay
from voice_platform_events.retry import RetryPolicy

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture(scope="module")
def event_database_url() -> Iterator[str]:
    admin_url = os.getenv("TEST_DATABASE_URL")
    if not admin_url or not os.getenv("TEST_REDIS_URL"):
        pytest.skip("TEST_DATABASE_URL and TEST_REDIS_URL required for event integration")
    database = f"event_test_{uuid4().hex}"
    url = make_url(admin_url).set(database=database).render_as_string(hide_password=False)

    async def database_command(command: str) -> None:
        engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as connection:
                await connection.execute(text(command))
        finally:
            await engine.dispose()

    asyncio.run(database_command(f'CREATE DATABASE "{database}"'))
    try:
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            env=os.environ | {"DATABASE_URL": url},
            check=True,
            capture_output=True,
        )
        yield url
    finally:
        asyncio.run(database_command(f'DROP DATABASE "{database}" WITH (FORCE)'))


@dataclass
class System:
    sessions: async_sessionmaker[AsyncSession]
    bus: RedisEventBus
    relay: OutboxRelay

    async def event(self, *, aggregate_id: UUID | None = None, version: int = 1) -> UUID:
        async with self.sessions.begin() as session:
            row = DomainEvent(
                event_type="lead.updated",
                producer="lead-service",
                aggregate_type="lead",
                aggregate_id=aggregate_id or uuid4(),
                aggregate_version=version,
                payload={},
            )
            session.add(row)
            await session.flush()
            return row.event_id

    def consumer(self, name: str = "worker-1", group: str = "test-group") -> Consumer:
        return Consumer(
            self.bus,
            self.sessions,
            group=group,
            name=name,
            handler=record_effect,
            max_attempts=3,
            timeout_seconds=1,
        )

    async def expire(self, group: str = "test-group") -> None:
        pending = await self.bus.redis.xpending_range(self.bus.stream, group, "-", "+", 100)
        for entry in pending:
            await self.bus.redis.xclaim(
                self.bus.stream,
                group,
                entry["consumer"],
                0,
                [entry["message_id"]],
                idle=60_000,
                justid=True,
            )

    async def effects(self) -> int:
        async with self.sessions() as session:
            return int(await session.scalar(select(func.count()).select_from(AuditLog)) or 0)


async def record_effect(event: EventEnvelope, session: AsyncSession) -> None:
    session.add(
        AuditLog(action="event-test", actor="worker", resource="event", resource_id=event.event_id)
    )


@pytest_asyncio.fixture
async def system(event_database_url: str) -> AsyncIterator[System]:
    engine = create_async_engine(event_database_url, pool_pre_ping=True)
    sessions = create_session_factory(engine)
    redis = Redis.from_url(
        os.environ["TEST_REDIS_URL"],
        decode_responses=True,
        socket_timeout=1,
        socket_connect_timeout=1,
    )
    bus = RedisEventBus(redis, stream=f"test.events.{uuid4().hex}", visibility_ms=100)
    try:
        yield System(sessions, bus, OutboxRelay(sessions, bus, RetryPolicy(max_attempts=3)))
    finally:
        await redis.delete(bus.stream, bus.dlq, f"{bus.dlq}.replayed")
        await redis.aclose()
        async with sessions.begin() as session:
            for model in (AuditLog, ProcessedEvent, DomainEvent, Lead):
                await session.execute(delete(model))
        await engine.dispose()


async def test_existing_lead_transaction_reaches_independent_groups(system: System) -> None:
    async with system.sessions.begin() as session:
        await LeadService(session).create_lead(
            LeadCreate(display_name="Event Lead", synthetic_profile_key="event-integration")
        )
    assert await system.relay.run_once()
    assert await system.relay.inspect() == {"unpublished": 0, "exhausted": 0}
    assert await system.consumer(group="analytics").run_once()
    assert await system.consumer(group="workflow").run_once()
    assert await system.effects() == 2
    assert not await system.consumer(group="workflow").run_once()


@pytest.mark.failure
async def test_rolled_back_lead_transaction_never_publishes(system: System) -> None:
    with pytest.raises(RuntimeError):
        async with system.sessions.begin() as session:
            await LeadService(session).create_lead(
                LeadCreate(display_name="Aborted Lead", synthetic_profile_key="event-aborted")
            )
            raise RuntimeError("abort")
    assert not await system.relay.run_once()
    assert await system.bus.redis.xlen(system.bus.stream) == 0


@pytest.mark.failure
async def test_crash_after_publish_redelivers_without_duplicate_effects(
    system: System,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await system.event()
    original = system.bus.publish

    async def publish_then_crash(event: EventEnvelope) -> str:
        await original(event)
        raise RuntimeError("process died before database commit")

    monkeypatch.setattr(system.bus, "publish", publish_then_crash)
    with pytest.raises(RuntimeError):
        await system.relay.run_once()
    monkeypatch.setattr(system.bus, "publish", original)
    assert await system.relay.run_once()
    assert await system.bus.redis.xlen(system.bus.stream) == 2
    consumer = system.consumer()
    assert await consumer.run_once()
    assert await consumer.run_once()
    assert await system.effects() == 1


@pytest.mark.failure
async def test_ack_failure_recovers_on_new_worker_without_duplicate_effects(
    system: System,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await system.event()
    await system.relay.run_once()
    original = system.bus.acknowledge

    async def fail_ack(_: Delivery) -> None:
        raise BusUnavailable("lost acknowledgement")

    monkeypatch.setattr(system.bus, "acknowledge", fail_ack)
    consumer = system.consumer()
    consumer.max_attempts = 1
    with pytest.raises(BusUnavailable):
        await consumer.run_once()
    assert await system.effects() == 1
    monkeypatch.setattr(system.bus, "acknowledge", original)
    await system.expire()
    replacement = system.consumer(name="replacement-worker")
    replacement.max_attempts = 1
    assert await replacement.run_once()
    assert await system.effects() == 1
    assert (await system.bus.redis.xpending(system.bus.stream, "test-group"))["pending"] == 0


@pytest.mark.failure
async def test_retry_rolls_back_effects_then_dead_letters_and_replays(system: System) -> None:
    await system.event()
    await system.relay.run_once()
    consumer = system.consumer()

    async def fail(event: EventEnvelope, session: AsyncSession) -> None:
        await record_effect(event, session)
        await session.flush()
        raise RuntimeError("synthetic failure")

    consumer.handler = fail
    for attempt in range(3):
        assert await consumer.run_once()
        assert await system.effects() == 0
        if attempt < 2:
            assert not await consumer.run_once()  # Backoff is not an immediate retry loop.
            await system.expire()
    letters = await system.bus.redis.xrange(system.bus.dlq)
    assert len(letters) == 1
    letter_id, fields = letters[0]
    assert fields["attempt"] == "3"
    assert "synthetic failure" not in fields["reason"]
    assert (await system.bus.redis.xpending(system.bus.stream, "test-group"))["pending"] == 0
    replay_id = await system.bus.replay_dead_letter(letter_id)
    assert await system.bus.replay_dead_letter(letter_id) == replay_id
    assert await system.consumer().run_once()
    assert await system.effects() == 1


@pytest.mark.failure
async def test_unknown_version_and_malformed_json_are_dead_lettered(system: System) -> None:
    for body in ("not-json", '{"event_version":2}'):
        await system.bus.redis.xadd(system.bus.stream, {"body": body})
        assert await system.consumer().run_once()
    assert await system.bus.redis.xlen(system.bus.dlq) == 2
    assert await system.effects() == 0


@pytest.mark.failure
async def test_dead_letter_failure_preserves_pending_delivery(system: System) -> None:
    await system.bus.redis.xadd(system.bus.stream, {"body": "malformed"})
    # Redis rejects XADD on a non-stream key; the script must not acknowledge.
    await system.bus.redis.set(system.bus.dlq, "wrong-type")
    with pytest.raises(BusUnavailable):
        await system.consumer().run_once()
    assert (await system.bus.redis.xpending(system.bus.stream, "test-group"))["pending"] == 1
    await system.bus.redis.delete(system.bus.dlq)
    await system.expire()
    assert await system.consumer(name="replacement").run_once()
    assert await system.bus.redis.xlen(system.bus.dlq) == 1


@pytest.mark.failure
async def test_redis_outage_exhaustion_and_explicit_recovery(system: System) -> None:
    event_id = await system.event()
    # Reserve a non-listening port; a real Redis client must fail its connection.
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        redis = Redis(
            host="127.0.0.1",
            port=reserved.getsockname()[1],
            socket_connect_timeout=0.2,
            decode_responses=True,
        )
        failing = OutboxRelay(system.sessions, RedisEventBus(redis), RetryPolicy(max_attempts=3))
        try:
            for _ in range(3):
                assert await failing.run_once()
                async with system.sessions.begin() as session:
                    row = await session.get(DomainEvent, event_id)
                    assert row is not None and row.published_at is None
                    assert row.next_attempt_at is not None
                    row.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
            assert not await failing.run_once()
            assert (await failing.inspect())["exhausted"] == 1
        finally:
            await redis.aclose()
    await system.relay.retry_event(event_id)
    assert await system.relay.run_once()
    assert await system.consumer().run_once()
    assert await system.effects() == 1


async def test_parallel_relays_preserve_aggregate_publication_order(system: System) -> None:
    aggregate = uuid4()
    ids = [await system.event(aggregate_id=aggregate, version=version) for version in (1, 2, 3)]
    for _ in range(3):
        await asyncio.gather(system.relay.run_once(), system.relay.run_once())
    rows = await system.bus.redis.xrange(system.bus.stream)
    assert [EventEnvelope.model_validate_json(fields["body"]).event_id for _, fields in rows] == ids


async def test_exhausted_aggregate_does_not_block_other_aggregates(system: System) -> None:
    aggregate = uuid4()
    first = await system.event(aggregate_id=aggregate)
    await system.event(aggregate_id=aggregate, version=2)
    independent = await system.event()
    async with system.sessions.begin() as session:
        await session.execute(
            update(DomainEvent).where(DomainEvent.event_id == first).values(publish_attempts=3)
        )
    assert await system.relay.run_once()
    assert not await system.relay.run_once()
    rows = await system.bus.redis.xrange(system.bus.stream)
    assert EventEnvelope.model_validate_json(rows[0][1]["body"]).event_id == independent


@pytest.mark.failure
async def test_stale_owner_cannot_acknowledge_reclaimed_delivery(system: System) -> None:
    await system.event()
    await system.relay.run_once()
    old = await system.bus.subscribe("test-group", "old-worker")
    assert old is not None
    await system.expire()
    new = await system.bus.subscribe("test-group", "new-worker")
    assert new is not None and new.attempt == 2
    await system.bus.acknowledge(old)
    await system.bus.dead_letter(old, "late-failure")
    assert (await system.bus.redis.xpending(system.bus.stream, "test-group"))["pending"] == 1
    assert await system.bus.redis.xlen(system.bus.dlq) == 0
    await system.bus.acknowledge(new)


async def test_concurrent_duplicate_deliveries_commit_one_effect(system: System) -> None:
    event_id = await system.event()
    await system.relay.run_once()
    async with system.sessions() as session:
        row = await session.get(DomainEvent, event_id)
        await system.bus.publish(EventEnvelope.model_validate(row))
    await asyncio.gather(
        system.consumer(name="first").run_once(), system.consumer(name="second").run_once()
    )
    assert await system.effects() == 1


@pytest.mark.failure
async def test_database_connection_loss_rolls_back_and_recovers(system: System) -> None:
    await system.event()
    await system.relay.run_once()
    consumer = system.consumer()

    async def disconnect(event: EventEnvelope, session: AsyncSession) -> None:
        await record_effect(event, session)
        await session.flush()
        # Terminate only this test transaction's connection, not the shared server.
        await session.execute(text("SELECT pg_terminate_backend(pg_backend_pid())"))

    consumer.handler = disconnect
    assert await consumer.run_once()
    assert await system.effects() == 0
    await system.expire()
    assert await system.consumer().run_once()
    assert await system.effects() == 1


@pytest.mark.failure
async def test_handler_timeout_and_cancellation_leave_delivery_pending(system: System) -> None:
    await system.event()
    await system.relay.run_once()
    started = asyncio.Event()

    async def blocked(event: EventEnvelope, session: AsyncSession) -> None:
        await record_effect(event, session)
        await session.flush()
        started.set()
        await asyncio.Event().wait()

    consumer = system.consumer()
    consumer.handler = blocked
    consumer.timeout_seconds = 0.05
    assert await consumer.run_once()
    assert await system.effects() == 0
    await system.expire()
    started.clear()
    consumer.timeout_seconds = 5
    task = asyncio.create_task(consumer.run_once())
    await asyncio.wait_for(started.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await system.effects() == 0
    await system.expire()
    assert await system.consumer(name="restarted").run_once()
    assert await system.effects() == 1


@pytest.mark.failure
async def test_invalid_outbox_version_is_quarantined_without_redis_write(system: System) -> None:
    event_id = await system.event()
    async with system.sessions.begin() as session:
        await session.execute(
            update(DomainEvent).where(DomainEvent.event_id == event_id).values(event_version=2)
        )
    assert await system.relay.run_once()
    assert (await system.relay.inspect())["exhausted"] == 1
    assert not await system.relay.run_once()
    assert await system.bus.redis.xlen(system.bus.stream) == 0


@pytest.mark.failure
async def test_redis_stream_loss_can_be_replayed_from_postgres(system: System) -> None:
    event_id = await system.event()
    await system.relay.run_once()
    assert await system.consumer().run_once()
    await system.bus.redis.delete(system.bus.stream)
    await system.relay.retry_event(event_id)
    assert await system.relay.run_once()
    # Existing effect survives Redis loss; new groups can rebuild their own state.
    assert await system.consumer().run_once()
    assert await system.effects() == 1
    assert await system.consumer(group="rebuild").run_once()
    assert await system.effects() == 2


@pytest.mark.failure
async def test_publication_backoff_starts_after_failed_attempt(
    system: System,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event_id = await system.event()
    failure_time = datetime.now(UTC)

    async def slow_failure(_: EventEnvelope) -> str:
        nonlocal failure_time
        await asyncio.sleep(0.03)
        failure_time = datetime.now(UTC)
        raise BusUnavailable("simulated slow failure")

    monkeypatch.setattr(system.bus, "publish", slow_failure)
    assert await system.relay.run_once()
    async with system.sessions() as session:
        row = await session.get(DomainEvent, event_id)
        assert row is not None and row.next_attempt_at is not None
        delay = system.relay.retry.delay(1, str(event_id))
        assert row.next_attempt_at >= failure_time + timedelta(seconds=delay)


@pytest.mark.failure
async def test_invalid_utf8_is_quarantined_without_poisoning_the_consumer(system: System) -> None:
    for body in (b"\xff", b'{"payload":{"text":"\xff"}}'):
        await system.bus.redis.xadd(system.bus.stream, {"body": body})
        assert await system.consumer().run_once()
    async with Redis.from_url(os.environ["TEST_REDIS_URL"], decode_responses=False) as raw:
        letters = await raw.xrange(system.bus.dlq)
    assert len(letters) == 2
    assert letters[0][1][b"body"] == b"\xff"
    assert (await system.bus.redis.xpending(system.bus.stream, "test-group"))["pending"] == 0
    await system.event()
    assert await system.relay.run_once()
    assert await system.consumer().run_once()
    assert await system.effects() == 1


@pytest.mark.failure
async def test_failed_commit_does_not_log_publication_as_committed(
    system: System,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await system.event()
    engine = system.sessions.kw["bind"].sync_engine

    def fail_commit(_: Connection) -> None:
        raise RuntimeError("simulated commit failure")

    event.listen(engine, "commit", fail_commit)
    try:
        with caplog.at_level("INFO"), pytest.raises(RuntimeError, match="commit failure"):
            await system.relay.run_once()
        assert "outbox.published" not in caplog.text
    finally:
        event.remove(engine, "commit", fail_commit)
    assert (await system.relay.inspect())["unpublished"] == 1
    assert await system.relay.run_once()
    assert await system.bus.redis.xlen(system.bus.stream) == 2

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from event_relay.__main__ import Settings
from pydantic import ValidationError
from voice_platform_config import ConfigurationError
from voice_platform_contracts.events import EventEnvelope
from voice_platform_events.retry import RetryPolicy


def test_retry_backoff_is_bounded_stable_and_jittered() -> None:
    policy = RetryPolicy()
    for attempt in range(1, 10):
        ceiling = min(30, 2 ** (attempt - 1))
        delay = policy.delay(attempt, "event-a")
        assert ceiling / 2 <= delay <= ceiling
        assert delay == policy.delay(attempt, "event-a")
    assert policy.delay(1, "event-a") != policy.delay(1, "event-b")
    with pytest.raises(ValueError):
        RetryPolicy(max_attempts=0)
    with pytest.raises(ValueError):
        policy.delay(0, "bad-attempt")


def test_envelope_roundtrip_and_version_boundary() -> None:
    event = EventEnvelope(
        event_id=uuid4(),
        event_type="lead.created",
        producer="lead-service",
        aggregate_type="lead",
        aggregate_id=uuid4(),
        aggregate_version=1,
        occurred_at=datetime.now(UTC),
        payload={"source": "synthetic"},
    )
    assert EventEnvelope.model_validate_json(event.model_dump_json()) == event
    for changes in (
        {"event_version": 2},
        {"aggregate_version": 0},
        {"occurred_at": "2026-01-01"},
        {"payload": {"value": float("nan")}},
    ):
        with pytest.raises(ValidationError):
            EventEnvelope.model_validate(event.model_dump() | changes)


def test_worker_config_fails_before_connecting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("POSTGRES_HOST", raising=False)
    with pytest.raises(ConfigurationError, match="POSTGRES_HOST"):
        Settings.from_env()
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://unused/unused")
    with pytest.raises(ValidationError, match="redis_url"):
        Settings.from_env()

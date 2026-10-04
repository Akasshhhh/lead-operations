"""Version 1 transport envelope; publication bookkeeping stays in PostgreSQL."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue


class EventEnvelope(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, from_attributes=True, allow_inf_nan=False
    )

    event_id: UUID
    event_type: str = Field(min_length=1, max_length=160, pattern=r"^[a-z][a-z0-9_.]*$")
    event_version: Literal[1] = 1
    producer: str = Field(min_length=1, max_length=120)
    aggregate_type: str = Field(min_length=1, max_length=80)
    aggregate_id: UUID
    aggregate_version: int = Field(ge=1)
    occurred_at: AwareDatetime
    correlation_id: UUID | None = None
    causation_id: UUID | None = None
    request_id: str | None = Field(default=None, max_length=120)
    trace_id: str | None = Field(default=None, max_length=64)
    idempotency_key: str | None = Field(default=None, max_length=160)
    payload: dict[str, JsonValue]

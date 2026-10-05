"""Content-free operational snapshots, separate from domain truth."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Metric(BaseModel):
    operation: str = Field(max_length=200)
    count: int = Field(ge=0)
    errors: int = Field(ge=0)
    total_ms: float = Field(ge=0)
    max_ms: float = Field(ge=0)


class Operation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service: str = Field(max_length=64)
    operation: str = Field(max_length=200)
    outcome: str = Field(max_length=32)
    duration_ms: float = Field(ge=0)
    request_id: str | None = Field(default=None, max_length=120)
    status: str | None = Field(default=None, max_length=3)
    conversation_id: str | None = Field(default=None, max_length=36)
    call_id: str | None = Field(default=None, max_length=36)
    turn_id: str | None = Field(default=None, max_length=36)
    provider: str | None = Field(default=None, max_length=64)


class OperationalSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal["process_local"]
    service: str = Field(max_length=64)
    uptime_seconds: float = Field(ge=0)
    metrics: list[Metric] = Field(max_length=128)
    recent: list[Operation] = Field(max_length=32)


class OperationalOverview(BaseModel):
    scope: Literal["process_local"] = "process_local"
    gateway: OperationalSnapshot
    lead: OperationalSnapshot | None
    conversation: OperationalSnapshot | None

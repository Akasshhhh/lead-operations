"""Bounded read-only dashboard views; no duplicate business state."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from .conversation import CallResponse, ConversationResponse


class ConversationListResponse(BaseModel):
    items: list[ConversationResponse]
    total: int
    limit: int
    offset: int


class CallListResponse(BaseModel):
    items: list[CallResponse]
    active_call: CallResponse | None
    total: int
    limit: int
    offset: int


class ConversationEvent(BaseModel):
    event_id: UUID
    event_type: str
    producer: str
    aggregate_type: str
    aggregate_id: UUID
    aggregate_version: int
    occurred_at: datetime
    published_at: datetime | None
    publish_attempts: int = Field(ge=0)


class ConversationEventsResponse(BaseModel):
    items: list[ConversationEvent]
    total: int
    limit: int
    offset: int

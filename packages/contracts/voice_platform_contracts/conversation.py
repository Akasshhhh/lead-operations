"""Public contracts for Conversation Service state and turn operations."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from .lead import QualificationFact, QualificationResponse

ConversationState = Literal[
    "CREATED",
    "CONNECTING",
    "GREETING",
    "DISCOVERY",
    "QUALIFICATION",
    "SCORING",
    "DECISION",
    "FOLLOW_UP",
    "HUMAN_HANDOFF",
    "COMPLETED",
    "FAILED",
]
CallStatus = Literal["CREATED", "CONNECTING", "CONNECTED", "RECONNECTING", "ENDED", "FAILED"]
Speaker = Literal["USER", "AGENT", "SYSTEM", "TOOL"]


class ConversationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    lead_id: UUID


class ConversationTransition(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    target_state: ConversationState
    expected_version: int = Field(ge=1, strict=True)
    reason: str | None = Field(default=None, min_length=1, max_length=160)
    context: dict[str, JsonValue] = Field(default_factory=dict, max_length=20)

    @model_validator(mode="after")
    def require_failure_reason(self) -> Self:
        if self.target_state == "FAILED" and not self.reason:
            raise ValueError("failure transitions require a reason")
        return self


class CallCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    transport: str = Field(default="WEBRTC", min_length=1, max_length=32)
    runtime_instance_id: str | None = Field(default=None, max_length=120)


class CallTransition(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    target_status: CallStatus
    expected_version: int = Field(ge=1, strict=True)
    reason: str | None = Field(default=None, min_length=1, max_length=160)

    @model_validator(mode="after")
    def require_failure_reason(self) -> Self:
        if self.target_status == "FAILED" and not self.reason:
            raise ValueError("failed call transitions require a reason")
        return self


class ConversationTurn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)

    turn_id: UUID
    call_id: UUID
    expected_version: int = Field(ge=1, strict=True)
    user_text: str = Field(min_length=1, max_length=20_000)
    facts: list[QualificationFact] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_text(self) -> Self:
        if "\x00" in self.user_text:
            raise ValueError("user_text cannot contain NUL characters")
        keys = [fact.field_key for fact in self.facts]
        if len(keys) != len(set(keys)):
            raise ValueError("each qualification field may occur only once per turn")
        return self


class AgentMessageCreate(BaseModel):
    """Completed generated output; persistence does not claim browser playback."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    message_id: UUID
    call_id: UUID
    expected_version: int = Field(ge=1, strict=True)
    parent_turn_id: UUID | None = None
    text: str = Field(min_length=1, max_length=20_000)
    provider: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    model: str = Field(min_length=1, max_length=128)

    @field_validator("text", "model")
    @classmethod
    def valid_text(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("NUL is not supported")
        try:
            value.encode("utf-8")
        except UnicodeError:
            raise ValueError("invalid UTF-8") from None
        return value


class ConversationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    lead_id: UUID
    state: ConversationState
    version: int
    started_at: datetime | None
    completed_at: datetime | None
    failure_reason: str | None
    failure_context: dict[str, JsonValue]
    next_action: str | None
    last_turn_at: datetime | None
    created_at: datetime
    updated_at: datetime


class CallResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    conversation_id: UUID
    transport: str
    status: CallStatus
    runtime_instance_id: str | None
    version: int
    reconnect_attempts: int
    connected_at: datetime | None
    ended_at: datetime | None
    failed_at: datetime | None
    disconnect_reason: str | None
    failure_reason: str | None
    created_at: datetime


class ConversationTurnResponse(BaseModel):
    conversation: ConversationResponse
    call: CallResponse
    message_id: UUID
    sequence_number: int
    turn_status: Literal["PENDING", "APPLIED", "FAILED"]
    qualification_error: str | None
    qualification: QualificationResponse | None
    next_action: str | None


class ConversationLiveState(BaseModel):
    conversation: ConversationResponse
    active_call: CallResponse | None
    qualification: QualificationResponse


class TranscriptQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    limit: int = Field(default=50, ge=1, le=100)
    before_sequence: int | None = Field(default=None, ge=1)
    after_sequence: int | None = Field(default=None, ge=0)
    speaker: Speaker | None = None
    query: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def validate_cursor_and_query(self) -> Self:
        if self.before_sequence is not None and self.after_sequence is not None:
            raise ValueError("before_sequence and after_sequence are mutually exclusive")
        if self.query is not None:
            if "\x00" in self.query:
                raise ValueError("query cannot contain NUL characters")
            self.query = self.query.strip() or None
        return self


class MessageHistoryEntry(BaseModel):
    id: UUID
    conversation_id: UUID
    call_id: UUID | None
    speaker: Speaker
    text: str
    sequence_number: int
    provider: str | None
    model: str | None
    message_metadata: dict[str, JsonValue]
    turn_status: Literal["PENDING", "APPLIED", "FAILED"]
    qualification_error: str | None
    redacted: bool
    redacted_at: datetime | None
    redaction_reason: str | None
    content_sha256: str | None
    created_at: datetime


class TranscriptHistoryEntry(BaseModel):
    id: UUID
    conversation_id: UUID
    call_id: UUID | None
    speaker: Speaker
    text: str
    segment_type: str
    sequence_number: int
    is_final: bool
    provider: str | None
    provider_segment_id: str | None
    started_at: datetime | None
    ended_at: datetime | None
    segment_metadata: dict[str, JsonValue]
    redacted: bool
    redacted_at: datetime | None
    redaction_reason: str | None
    content_sha256: str | None
    created_at: datetime


class MessageHistoryResponse(BaseModel):
    items: list[MessageHistoryEntry]
    has_more: bool
    next_before_sequence: int | None
    next_after_sequence: int | None


class TranscriptHistoryResponse(BaseModel):
    items: list[TranscriptHistoryEntry]
    has_more: bool
    next_before_sequence: int | None
    next_after_sequence: int | None

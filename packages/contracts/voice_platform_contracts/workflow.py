"""Bounded caller-request actions and demo workflow operations; no generic engine."""

from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .conversation import ConversationResponse, MessageHistoryEntry
from .qualification import BoundedInput

WorkflowAction = Literal["HUMAN_HANDOFF", "FOLLOW_UP", "END_CONVERSATION"]
HandoffStatus = Literal["REQUESTED", "ASSIGNED", "COMPLETED", "CANCELLED"]
FollowUpStatus = Literal["SCHEDULED", "RUNNING", "READY", "COMPLETED", "CANCELLED", "FAILED"]


class ActionProposal(BoundedInput):
    action: WorkflowAction
    evidence: str = Field(min_length=1, max_length=2000)
    scheduled_at: datetime | None = None

    @model_validator(mode="after")
    def scheduling(self) -> Self:
        if self.scheduled_at is not None:
            if self.action != "FOLLOW_UP" or self.scheduled_at.tzinfo is None:
                raise ValueError("only follow-ups accept timezone-aware schedules")
        return self


class WorkflowActionCreate(ActionProposal):
    action_id: UUID
    turn_id: UUID
    call_id: UUID
    expected_version: int = Field(ge=1, strict=True)
    provider: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    model: str = Field(min_length=1, max_length=128)


class HandoffResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    lead_id: UUID
    conversation_id: UUID
    reason: str
    priority: str
    summary: str
    status: HandoffStatus
    requested_at: datetime
    assigned_at: datetime | None
    completed_at: datetime | None


class FollowUpResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    lead_id: UUID
    conversation_id: UUID | None
    followup_type: str
    scheduled_at: datetime
    status: FollowUpStatus
    attempt: int
    max_attempts: int
    reason: str | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


class WorkflowActionResponse(BaseModel):
    action_id: UUID
    action: WorkflowAction
    policy_version: Literal["caller-request-v1"] = "caller-request-v1"
    conversation: ConversationResponse
    acknowledgement: MessageHistoryEntry
    handoff: HandoffResponse | None = None
    follow_up: FollowUpResponse | None = None
    close_media: bool = True


class WorkflowSnapshot(BaseModel):
    handoffs: list[HandoffResponse]
    follow_ups: list[FollowUpResponse]


class HandoffTransition(BoundedInput):
    operation_id: UUID
    expected_status: HandoffStatus
    target_status: HandoffStatus


class FollowUpTransition(BoundedInput):
    operation_id: UUID
    expected_status: FollowUpStatus
    target_status: Literal["SCHEDULED", "COMPLETED", "CANCELLED"]


class AgentOutputCheck(BoundedInput):
    turn_id: UUID
    call_id: UUID
    text: str = Field(min_length=1, max_length=2000)


class AgentOutputDecision(BaseModel):
    text: str
    allowed: bool
    reason: Literal["allowed", "unsupported_claim"]
    policy_version: Literal["response-policy-v1"] = "response-policy-v1"


class FollowUpDispatch(BaseModel):
    claimed: int = 0
    ready: int = 0
    failed: int = 0
    stale: int = 0

    @field_validator("claimed", "ready", "failed", "stale")
    @classmethod
    def nonnegative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("counts must be nonnegative")
        return value

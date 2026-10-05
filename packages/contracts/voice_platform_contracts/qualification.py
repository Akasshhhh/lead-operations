"""Additive staged qualification and evidence contracts; no score input exists."""

import json
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from .conversation import ConversationTurn
from .lead import LeadResponse, QualificationFact, QualificationResponse

QualificationField = Literal[
    "education_level",
    "years_experience",
    "english_level",
    "has_job_offer",
    "budget_ready",
    "urgency",
]


class BoundedInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)

    @model_validator(mode="after")
    def storage_safe(self) -> Self:
        encoded = json.dumps(self.model_dump(mode="json"), ensure_ascii=False, allow_nan=False)
        if "\x00" in encoded or "\\u0000" in encoded or len(encoded.encode("utf-8")) > 65_536:
            raise ValueError("invalid or oversized input")
        return self


class StagedTurnCreate(BoundedInput):
    turn_id: UUID
    call_id: UUID
    expected_version: int = Field(ge=1, strict=True)
    user_text: str = Field(min_length=1, max_length=20_000)

    def as_turn(self) -> ConversationTurn:
        return ConversationTurn.model_validate(self.model_dump())


class FactProposal(BoundedInput):
    field_key: QualificationField
    value: JsonValue
    evidence: str = Field(min_length=1, max_length=2000)
    resolve_conflict: bool = False


class ProposedFacts(BoundedInput):
    proposals: list[FactProposal] = Field(default_factory=list, max_length=6)
    provider: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    model: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def unique_fields(self) -> Self:
        keys = [proposal.field_key for proposal in self.proposals]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate field")
        return self


class ValidateProposals(ProposedFacts):
    user_text: str = Field(min_length=1, max_length=20_000)


class ValidatedFacts(BaseModel):
    facts: list[QualificationFact]
    provenance: list[dict[str, JsonValue]]


class QualificationPlan(BaseModel):
    qualification: QualificationResponse
    missing_fields: list[str]
    provisional_fields: list[str]
    contradictory_fields: list[str]
    next_field: str | None
    next_question: str | None


class QualificationContext(BaseModel):
    lead: LeadResponse
    plan: QualificationPlan

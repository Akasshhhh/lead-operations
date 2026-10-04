"""Versioned public contracts for Lead Service interactions."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

AnswerKey = Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")]
QualificationFactStatus = Literal["PROVISIONAL", "CONFIRMED", "CONTRADICTORY"]


class LeadMutation(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_storage_values(self) -> Self:
        def has_nul(value: object) -> bool:
            if isinstance(value, str):
                return "\x00" in value
            if isinstance(value, dict):
                return any(has_nul(key) or has_nul(item) for key, item in value.items())
            if isinstance(value, list):
                return any(has_nul(item) for item in value)
            return False

        values = self.model_dump()
        if has_nul(values):
            raise ValueError("NUL characters are not supported")
        # JSONB cannot store non-finite numbers or unpaired Unicode surrogates.
        try:
            encoded = json.dumps(values, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (ValueError, UnicodeError) as exc:
            raise ValueError("invalid JSON value") from exc
        if len(encoded) > 65_536:
            raise ValueError("lead mutation exceeds 64 KiB")
        return self


class LeadCreate(LeadMutation):
    display_name: str = Field(min_length=1, max_length=160)
    synthetic_profile_key: str = Field(
        min_length=1,
        max_length=120,
        pattern=r"^[a-z0-9][a-z0-9-]*$",
    )
    intent: str | None = Field(default=None, max_length=160)
    target_country: str | None = Field(default=None, max_length=80)
    preferred_language: str = Field(default="en", min_length=2, max_length=16)
    status: str = Field(default="NEW", min_length=1, max_length=32)
    initial_answers: dict[AnswerKey, JsonValue] = Field(default_factory=dict, max_length=100)


class LeadUpdate(LeadMutation):
    display_name: str | None = Field(default=None, min_length=1, max_length=160)
    intent: str | None = Field(default=None, max_length=160)
    target_country: str | None = Field(default=None, max_length=80)
    preferred_language: str | None = Field(default=None, min_length=2, max_length=16)
    status: str | None = Field(default=None, min_length=1, max_length=32)
    expected_version: int = Field(ge=1, le=2_147_483_646, strict=True)

    @model_validator(mode="after")
    def validate_changes(self) -> Self:
        if not self.model_fields_set - {"expected_version"}:
            raise ValueError("at least one mutable field is required")
        for name in ("display_name", "preferred_language", "status"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self


class LeadResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    display_name: str
    synthetic_profile_key: str
    intent: str | None
    target_country: str | None
    preferred_language: str
    status: str
    created_at: datetime
    updated_at: datetime
    version: int


class LeadListResponse(BaseModel):
    items: list[LeadResponse]
    total: int
    limit: int
    offset: int


class QualificationAnswerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    field_key: str
    value: object
    conflict_value: object | None = None
    normalized_value: str | None
    confidence: float | None
    answer_status: str
    source: str
    conversation_id: UUID | None
    updated_at: datetime


class LeadScoreResponse(BaseModel):
    score: int = Field(ge=0, le=100)
    classification: str
    rule_version: str
    reasons: list[JsonValue]
    calculated_at: datetime


class QualificationResponse(BaseModel):
    profile_id: UUID
    lead_id: UUID
    status: str
    completeness: int
    version: int
    answers: list[QualificationAnswerResponse]
    score: LeadScoreResponse | None = None


class QualificationFact(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)

    field_key: AnswerKey
    value: JsonValue
    status: QualificationFactStatus = "PROVISIONAL"
    confidence: float | None = Field(default=None, ge=0, le=1)
    resolve_conflict: bool = False


class QualificationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)

    conversation_id: UUID
    turn_id: UUID
    expected_profile_version: int = Field(ge=1, strict=True)
    facts: list[QualificationFact] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_unique_facts(self) -> Self:
        keys = [fact.field_key for fact in self.facts]
        if len(keys) != len(set(keys)):
            raise ValueError("each qualification field may occur only once per update")
        return self


class HealthResponse(BaseModel):
    status: str
    database: str

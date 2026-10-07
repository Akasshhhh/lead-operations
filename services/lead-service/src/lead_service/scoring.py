"""Lead-owned, deterministic baseline scoring rules for live qualification."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

RULE_VERSION = "baseline-v1"
FIELD_WEIGHTS: dict[str, int] = {
    "education_level": 10,
    "years_experience": 10,
    "english_level": 10,
    "has_job_offer": 10,
    "budget_ready": 10,
    "urgency": 10,
}

_EDUCATION = {"none", "high-school", "diploma", "bachelors", "masters", "doctorate"}
_ENGLISH = {"beginner", "intermediate", "advanced"}
_URGENCY = {"low", "medium", "high"}


@dataclass(frozen=True, slots=True)
class ScoreResult:
    score: int
    classification: str
    completeness: int
    profile_status: str
    reasons: list[dict[str, Any]]
    contradictions: list[str]


def valid_field_value(field_key: str, value: object) -> bool:
    """Return whether a supported baseline field has a valid known value."""
    # Intake values are validated here but deliberately absent from FIELD_WEIGHTS.
    if field_key in {"target_country", "visa_type"}:
        return (
            isinstance(value, str)
            and 1 <= len(value) <= 80
            and bool(re.fullmatch(r"[a-zA-Z][a-zA-Z0-9 .'-]*", value))
        )
    if field_key == "education_level":
        return isinstance(value, str) and value.strip().lower() in _EDUCATION
    if field_key == "years_experience":
        return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 80
    if field_key == "english_level":
        return isinstance(value, str) and value.strip().lower() in _ENGLISH
    if field_key in {"has_job_offer", "budget_ready"}:
        return isinstance(value, bool)
    if field_key == "urgency":
        return isinstance(value, str) and value.strip().lower() in _URGENCY
    return True


def evaluate_answers(answers: list[Any]) -> ScoreResult:
    """Calculate the baseline from persisted answer rows only."""
    reasons: list[dict[str, Any]] = []
    contradictions: list[str] = []
    confirmed = 0
    score = 0
    for answer in answers:
        field_key = answer.field_key
        if field_key not in FIELD_WEIGHTS:
            continue
        if answer.answer_status == "CONTRADICTORY":
            contradictions.append(field_key)
            continue
        if answer.answer_status != "CONFIRMED" or not valid_field_value(field_key, answer.value):
            continue
        weight = FIELD_WEIGHTS[field_key]
        score += weight
        confirmed += 1
        reasons.append({"field_key": field_key, "weight": weight, "value": answer.value})

    completeness = round(confirmed * 100 / len(FIELD_WEIGHTS))
    if contradictions:
        profile_status = "CONTRADICTORY"
    elif confirmed == len(FIELD_WEIGHTS):
        profile_status = "COMPLETE"
    else:
        profile_status = "INCOMPLETE"
    classification = "COLD" if score < 20 else "WARM" if score < 40 else "HOT"
    return ScoreResult(
        score=score,
        classification=classification,
        completeness=completeness,
        profile_status=profile_status,
        reasons=reasons,
        contradictions=sorted(set(contradictions)),
    )

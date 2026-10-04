from __future__ import annotations

from typing import Any, cast

import pytest
from lead_service.schemas import LeadCreate, LeadUpdate
from pydantic import ValidationError


def test_lead_create_accepts_structured_initial_answers() -> None:
    lead = LeadCreate(
        display_name="Synthetic Lead",
        synthetic_profile_key="synthetic-lead-1",
        initial_answers={"years_experience": 5, "has_job_offer": False},
    )

    assert lead.initial_answers["years_experience"] == 5


def test_lead_create_rejects_invalid_profile_key() -> None:
    with pytest.raises(ValidationError):
        LeadCreate(display_name="Synthetic Lead", synthetic_profile_key="Not valid")


def test_lead_create_rejects_unbounded_answer_keys_and_values() -> None:
    with pytest.raises(ValidationError):
        LeadCreate(
            display_name="Synthetic Lead",
            synthetic_profile_key="synthetic-lead-1",
            initial_answers={"Not valid": cast(Any, object())},
        )


def test_lead_update_requires_a_mutation() -> None:
    with pytest.raises(ValidationError, match="at least one mutable field"):
        LeadUpdate(expected_version=1)


@pytest.mark.parametrize(
    "value", ["bad\x00text", "\ud800", float("nan"), float("inf"), "x" * 65_536]
)
def test_storage_invalid_answer_values_are_rejected(value: Any) -> None:
    with pytest.raises(ValidationError):
        LeadCreate(
            display_name="Lead",
            synthetic_profile_key="safe-key",
            initial_answers={"answer": {"nested": [value]}},
        )


def test_nullable_patch_and_literal_unicode_escape_remain_valid() -> None:
    update = LeadUpdate(intent=None, expected_version=1)
    assert update.model_dump(exclude_unset=True) == {"intent": None, "expected_version": 1}
    LeadCreate(display_name=r"A literal \u0000", synthetic_profile_key="literal-text")

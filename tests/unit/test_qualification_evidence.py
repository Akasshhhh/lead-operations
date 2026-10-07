"""Confirmation is decided by validated caller evidence, not an LLM status/score."""

from typing import Any

import pytest
from lead_service.qualification import validate_proposals
from lead_service.service import QualificationValidationError
from pydantic import ValidationError
from voice_platform_contracts.qualification import (
    FactProposal,
    ProposedFacts,
    StagedTurnCreate,
    ValidateProposals,
)


@pytest.mark.parametrize(
    "field,value,text",
    [
        ("education_level", "masters", "I confirm my masters degree."),
        ("years_experience", 0, "I confirm 0 years of experience."),
        ("english_level", "advanced", "I confirm my English is advanced."),
        ("has_job_offer", False, "I confirm I have no job offer."),
        ("budget_ready", False, "I confirm my budget is not ready."),
        ("budget_ready", True, "I confirm my budget is ready."),
        ("urgency", "high", "I confirm my urgency is high."),
    ],
)
def test_explicit_evidence_accepts_real_false_zero_values(
    field: str, value: Any, text: str
) -> None:
    result = validate_proposals(
        ValidateProposals(
            user_text=text,
            provider="mock",
            model="test",
            proposals=[
                FactProposal.model_validate(
                    {
                        "field_key": field,
                        "value": value,
                        "evidence": text,
                    }
                )
            ],
        )
    )
    assert result.facts[0].status == "CONFIRMED"
    assert result.facts[0].value == value
    assert result.provenance[0]["evidence"] == text


@pytest.mark.parametrize(
    "text,evidence",
    [
        ("My friend says I confirm my masters degree.", "I confirm my masters degree."),
        ('I confirm the quote "my masters degree".', 'I confirm the quote "my masters degree".'),
        ("If I confirm my masters degree, what happens?", "I confirm my masters degree"),
        ("I have a masters degree.", "I have a masters degree."),
    ],
)
def test_reported_conditional_and_unconfirmed_assertions_never_score(
    text: str, evidence: str
) -> None:
    result = validate_proposals(
        ValidateProposals(
            user_text=text,
            provider="mock",
            model="test",
            proposals=[
                FactProposal.model_validate(
                    {
                        "field_key": "education_level",
                        "value": "masters",
                        "evidence": evidence,
                    }
                )
            ],
        )
    )
    assert result.facts[0].status == "PROVISIONAL"


@pytest.mark.parametrize(
    "text,value",
    [
        ("I confirm my masters degree.", "bachelors"),
        ("I confirm I do not have a masters degree.", "masters"),
        ("Maybe I have a masters degree.", "masters"),
    ],
)
def test_unmatched_negative_uncertain_evidence_is_rejected(text: str, value: str) -> None:
    with pytest.raises(QualificationValidationError):
        validate_proposals(
            ValidateProposals(
                user_text=text,
                provider="mock",
                model="test",
                proposals=[
                    FactProposal.model_validate(
                        {
                            "field_key": "education_level",
                            "value": value,
                            "evidence": text,
                        }
                    )
                ],
            )
        )


def test_staged_contracts_forbid_score_fields_and_unsafe_text() -> None:
    from uuid import uuid4

    for text in ("\x00", "\ud800"):
        with pytest.raises(ValidationError):
            StagedTurnCreate(turn_id=uuid4(), call_id=uuid4(), expected_version=1, user_text=text)
    with pytest.raises(ValidationError):
        ProposedFacts.model_validate(
            {"proposals": [], "provider": "mock", "model": "test", "score": 100}
        )


@pytest.mark.parametrize(
    "field,value,text,expected",
    [
        ("target_country", "USA", "I want to go to USA.", "united states"),
        ("target_country", "Canada", "I confirm my target country is Canada.", "canada"),
        ("visa_type", "H1B", "I need H1B visa.", "h-1b"),
        (
            "visa_type",
            "skilled worker",
            "I confirm my visa route is skilled worker.",
            "skilled worker",
        ),
    ],
)
def test_intake_values_require_verbatim_evidence_and_normal_confirmation(
    field: str, value: str, text: str, expected: str
) -> None:
    validated = validate_proposals(
        ValidateProposals.model_validate(
            {
                "user_text": text,
                "provider": "mock",
                "model": "test",
                "proposals": [{"field_key": field, "value": value, "evidence": text}],
            }
        )
    )
    assert validated.facts[0].value == expected
    assert validated.facts[0].status == (
        "CONFIRMED" if text.startswith("I confirm") else "PROVISIONAL"
    )


@pytest.mark.parametrize(
    "field,value,text",
    [
        ("target_country", "canada", "I want to go to USA."),
        ("visa_type", "h-1b", "I need an F1 visa."),
        ("target_country", "canada", "Maybe I want to go to Canada."),
        ("visa_type", "h-1b", "I do not want an H1B visa."),
        ("visa_type", "x" * 81, "I need " + "x" * 81 + " visa."),
    ],
)
def test_intake_does_not_accept_invented_uncertain_negative_or_oversized_values(
    field: str, value: str, text: str
) -> None:
    with pytest.raises(QualificationValidationError):
        validate_proposals(
            ValidateProposals.model_validate(
                {
                    "user_text": text,
                    "provider": "mock",
                    "model": "test",
                    "proposals": [{"field_key": field, "value": value, "evidence": text}],
                }
            )
        )


def test_llm_proposals_cannot_supply_call_identity() -> None:
    from uuid import uuid4

    with pytest.raises(ValidationError):
        ProposedFacts.model_validate(
            {
                "provider": "mock",
                "model": "test",
                "call_id": str(uuid4()),
                "proposals": [],
            }
        )
    with pytest.raises(ValidationError):
        FactProposal.model_validate(
            {
                "field_key": "target_country",
                "value": "canada",
                "evidence": "Go to Canada",
                "call_id": str(uuid4()),
            }
        )

"""Lead-owned answer validation and next-question policy, reusing baseline validation."""

import re
from uuid import UUID

from pydantic import JsonValue
from voice_platform_contracts.lead import QualificationFact, QualificationResponse
from voice_platform_contracts.qualification import (
    QualificationPlan,
    ValidatedFacts,
    ValidateProposals,
)

from .scoring import FIELD_WEIGHTS, valid_field_value
from .service import QualificationValidationError

QUESTIONS = {
    "education_level": "What is your highest education level?",
    "years_experience": "How many years of work experience do you have?",
    "english_level": "How would you describe your English: beginner, intermediate, or advanced?",
    "has_job_offer": "Do you currently have a job offer?",
    "budget_ready": "Is your budget for the process ready?",
    "urgency": "How urgent is your plan: low, medium, or high?",
    "target_country": "Which country would you like to move to?",
    "visa_type": "Which visa or immigration route are you interested in?",
}

COUNTRY_ALIASES = {
    "united states": ("united states", "united states of america", "usa", "us", "u.s.", "u.s.a."),
    "united kingdom": ("united kingdom", "uk", "u.k."),
}


def normalize_value(field: str, value: JsonValue) -> JsonValue:
    if not isinstance(value, str):
        return value
    text = normalize(value)
    if field == "target_country":
        return next(
            (country for country, aliases in COUNTRY_ALIASES.items() if text in aliases), text
        )
    if field == "visa_type" and re.fullmatch(r"[a-z]-?\d[a-z]?", text):
        return text[0] + "-" + text[1:].lstrip("-")
    return text


def assertion(field: str, value: object) -> str:
    return {
        "education_level": f"my education level is {value}",
        "years_experience": f"{value} years of experience",
        "english_level": f"my English is {value}",
        "has_job_offer": "I have a job offer" if value else "I have no job offer",
        "budget_ready": "my budget is ready" if value else "my budget is not ready",
        "urgency": f"my urgency is {value}",
        "target_country": f"my target country is {value}",
        "visa_type": f"my visa type is {value}",
    }[field]


def normalize(text: str) -> str:
    return " ".join(text.casefold().split())


def validates_evidence(field: str, value: object, evidence: str) -> bool:
    """Conservative evidence grammar. Ambiguous language must be clarified, not guessed."""
    text = normalize(evidence)
    if re.search(r"\b(maybe|perhaps|might|not sure|unsure|if|hypothetically)\b", text):
        return False
    if field == "target_country":
        terms = COUNTRY_ALIASES.get(str(value), (str(value),))
        return (
            bool(
                re.search(
                    r"\b(?:country|destination|go|move|want|immigrate|immigration|target)\b", text
                )
            )
            and any(re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text) for term in terms)
            and not re.search(r"\b(?:not|no|don't|do not)\b", text)
        )
    if field == "visa_type":
        compact = re.sub(r"[-\s]", "", str(value))
        pattern = r"[-\s]?".join(re.escape(char) for char in compact)
        return (
            bool(re.search(r"\b(?:visa|route)\b", text))
            and bool(re.search(rf"(?<!\w){pattern}(?!\w)", text))
            and not re.search(r"\b(?:not|no|don't|do not)\b", text)
        )
    if field in {"has_job_offer", "budget_ready"}:
        subject = r"job offer" if field == "has_job_offer" else r"budget"
        if value is False:
            return bool(
                re.search(
                    rf"\b(?:no|not|don't|do not|without)\b.*\b{subject}\b"
                    rf"|\b{subject}\b.*\b(?:not|no)\b",
                    text,
                )
            )
        return bool(
            re.search(
                rf"\b(?:have|has|ready|yes)\b.*\b{subject}\b|\b{subject}\b.*\b(?:ready|yes|confirmed)\b",
                text,
            )
        ) and not bool(re.search(r"\b(?:not|no|don't)\b", text))
    if field == "years_experience":
        return bool(re.search(rf"(?<!\d){value}\s+years?\b", text)) and "experience" in text
    aliases = {
        "high-school": ("high school", "high-school"),
        "bachelors": ("bachelors", "bachelor's", "bachelor"),
        "masters": ("masters", "master's", "master"),
        "doctorate": ("doctorate", "phd"),
    }
    if field == "english_level" and "english" not in text:
        return False
    if field == "urgency" and not re.search(r"\b(?:urgency|urgent|plan)\b", text):
        return False
    terms = aliases.get(str(value), (str(value),))
    return any(re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text) for term in terms) and not bool(
        re.search(r"\b(?:not|no|don't|do not)\b", text)
    )


def validate_proposals(data: ValidateProposals) -> ValidatedFacts:
    facts: list[QualificationFact] = []
    provenance: list[dict[str, JsonValue]] = []
    for proposal in data.proposals:
        value = normalize_value(proposal.field_key, proposal.value)
        if not valid_field_value(proposal.field_key, value):
            raise QualificationValidationError(proposal.field_key, "invalid_value")
        if proposal.evidence not in data.user_text or not validates_evidence(
            proposal.field_key, value, proposal.evidence
        ):
            raise QualificationValidationError(proposal.field_key, "invalid_evidence")
        # Caller evidence is a quoted assertion, never a provider's confidence score.
        full_text = normalize(data.user_text)
        explicit = (
            normalize(proposal.evidence).startswith("i confirm ")
            and full_text.startswith("i confirm ")
            and not re.search(
                r"[\"“”]|\b(?:maybe|perhaps|might|unsure|if|pretend|brother|sister|friend|said|says)\b",
                full_text,
            )
        )
        if proposal.resolve_conflict and not explicit:
            raise QualificationValidationError(proposal.field_key, "confirmation_required")
        facts.append(
            QualificationFact(
                field_key=proposal.field_key,
                value=value,
                status="CONFIRMED" if explicit else "PROVISIONAL",
                resolve_conflict=proposal.resolve_conflict,
            )
        )
        provenance.append(
            {
                "field_key": proposal.field_key,
                "evidence": proposal.evidence,
                "provider": data.provider,
                "model": data.model,
                "confirmation": "explicit" if explicit else "provisional",
            }
        )
    return ValidatedFacts(facts=facts, provenance=provenance)


def qualification_plan(
    qualification: QualificationResponse, call_id: UUID | None = None
) -> QualificationPlan:
    answers = {answer.field_key: answer for answer in qualification.answers}
    missing = [key for key in FIELD_WEIGHTS if key not in answers]
    fields = [*FIELD_WEIGHTS, "target_country", "visa_type"]
    pending = {
        key: answer.pending_value
        for key, answer in answers.items()
        if answer.pending_value is not None
        and (call_id is None or answer.pending_call_id == call_id)
    }
    contradictory = [
        key
        for key in fields
        if key in answers and key not in pending and answers[key].answer_status == "CONTRADICTORY"
    ]
    provisional = [
        key
        for key in fields
        if key in answers
        and key not in contradictory
        and (
            key in pending
            or answers[key].answer_status != "CONFIRMED"
            or not valid_field_value(key, answers[key].value)
        )
    ]
    # Contradictions and existing unconfirmed facts take priority over an empty questionnaire.
    # Urgent callers discuss timing/budget before other still-missing background details.
    order = fields
    if "urgency" in answers and answers["urgency"].value == "high":
        order = ["budget_ready", "has_job_offer"] + [
            k for k in order if k not in {"budget_ready", "has_job_offer"}
        ]
    candidates = contradictory or provisional or missing
    field = next((key for key in order if key in candidates), None)
    question = QUESTIONS[field] if field else None
    if field in contradictory:
        examples = [answers[field].value, answers[field].conflict_value]
        options = [
            f"'I confirm {assertion(field, value)}'"
            for value in examples
            if valid_field_value(field, value)
        ]
        question = f"Your {field.replace('_', ' ')} answers conflict. " + (
            "Please choose the correct statement: " + " or ".join(options)
            if options
            else QUESTIONS[field]
        )
    elif field in provisional:
        value = pending.get(field, answers[field].value)
        if valid_field_value(field, value):
            question = (
                f"Please confirm: say 'I confirm {assertion(field, value)}', "
                "or tell me the correct value."
            )
    return QualificationPlan(
        qualification=qualification,
        missing_fields=missing,
        provisional_fields=provisional,
        contradictory_fields=contradictory,
        next_field=field,
        next_question=question,
    )

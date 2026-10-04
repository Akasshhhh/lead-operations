from conversation_service.state_machine import (
    InvalidTransitionError,
    ensure_call_transition,
    ensure_conversation_transition,
)
from lead_service.scoring import RULE_VERSION, evaluate_answers, valid_field_value


class Answer:
    def __init__(self, field_key: str, value: object, status: str) -> None:
        self.field_key = field_key
        self.value = value
        self.answer_status = status


def test_conversation_graph_is_explicit_and_terminal() -> None:
    ensure_conversation_transition("CREATED", "CONNECTING")
    ensure_conversation_transition("DISCOVERY", "QUALIFICATION")
    ensure_conversation_transition("QUALIFICATION", "DISCOVERY")
    ensure_conversation_transition("DECISION", "HUMAN_HANDOFF")
    ensure_conversation_transition("HUMAN_HANDOFF", "COMPLETED")
    try:
        ensure_conversation_transition("COMPLETED", "DISCOVERY")
    except InvalidTransitionError:
        pass
    else:
        raise AssertionError("terminal conversation state was mutable")


def test_call_graph_separates_reconnect_from_conversation_state() -> None:
    ensure_call_transition("CONNECTED", "RECONNECTING")
    ensure_call_transition("RECONNECTING", "CONNECTED")
    ensure_call_transition("CONNECTED", "FAILED")


def test_baseline_score_counts_only_confirmed_valid_fields() -> None:
    result = evaluate_answers(
        [
            Answer("education_level", "masters", "CONFIRMED"),
            Answer("years_experience", 0, "CONFIRMED"),
            Answer("english_level", "advanced", "PROVISIONAL"),
            Answer("has_job_offer", False, "CONFIRMED"),
            Answer("budget_ready", True, "CONTRADICTORY"),
            Answer("urgency", "high", "CONFIRMED"),
        ]
    )
    assert result.score == 40
    assert result.classification == "HOT"
    assert result.completeness == 67
    assert result.profile_status == "CONTRADICTORY"
    assert result.contradictions == ["budget_ready"]
    assert RULE_VERSION == "baseline-v1"


def test_baseline_values_are_validated_without_domain_scoring() -> None:
    assert valid_field_value("years_experience", 0)
    assert valid_field_value("has_job_offer", False)
    assert not valid_field_value("years_experience", True)
    assert not valid_field_value("education_level", "unknown")
    assert valid_field_value("human_requested", True)

"""Explicit, backend-owned Conversation and call-session transition graphs."""

from __future__ import annotations

CONVERSATION_TRANSITIONS: dict[str, frozenset[str]] = {
    "CREATED": frozenset({"CONNECTING", "FAILED"}),
    "CONNECTING": frozenset({"GREETING", "FAILED"}),
    "GREETING": frozenset({"DISCOVERY", "FAILED"}),
    "DISCOVERY": frozenset({"QUALIFICATION", "FAILED"}),
    "QUALIFICATION": frozenset({"DISCOVERY", "SCORING", "FAILED"}),
    "SCORING": frozenset({"QUALIFICATION", "DECISION", "FAILED"}),
    "DECISION": frozenset({"QUALIFICATION", "FOLLOW_UP", "HUMAN_HANDOFF", "COMPLETED", "FAILED"}),
    "FOLLOW_UP": frozenset({"COMPLETED", "FAILED"}),
    "HUMAN_HANDOFF": frozenset({"COMPLETED", "FAILED"}),
    "COMPLETED": frozenset(),
    "FAILED": frozenset(),
}

CALL_TRANSITIONS: dict[str, frozenset[str]] = {
    "CREATED": frozenset({"CONNECTING"}),
    "CONNECTING": frozenset({"CONNECTED", "FAILED"}),
    "CONNECTED": frozenset({"RECONNECTING", "ENDED", "FAILED"}),
    "RECONNECTING": frozenset({"CONNECTED", "FAILED"}),
    "ENDED": frozenset(),
    "FAILED": frozenset(),
}


class InvalidTransitionError(ValueError):
    """Raised when a state transition is not present in the graph."""


def ensure_conversation_transition(current: str, target: str) -> None:
    if target not in CONVERSATION_TRANSITIONS.get(current, frozenset()):
        raise InvalidTransitionError(f"conversation cannot transition from {current} to {target}")


def ensure_call_transition(current: str, target: str) -> None:
    if target not in CALL_TRANSITIONS.get(current, frozenset()):
        raise InvalidTransitionError(f"call cannot transition from {current} to {target}")

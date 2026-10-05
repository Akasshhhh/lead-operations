"""Explicit English demo consent and response policies, not semantic/identity proof."""

import re
from datetime import UTC, datetime, timedelta

from voice_platform_contracts.workflow import ActionProposal, AgentOutputDecision, WorkflowAction


class WorkflowPolicyError(ValueError):
    """A proposal lacks supported caller evidence or a permitted schedule."""


HUMAN = re.compile(
    r"(?:please )?(?:"
    r"(?:i (?:want|need|would like) to|can i|let me) (?:speak|talk) to (?:a |an )?"
    r"(?:human|consultant|advisor|person)|"
    r"(?:connect|transfer) me to (?:a |an )?(?:human|consultant|advisor|person)|"
    r"i (?:want|need|would like) (?:a |an )?(?:human|consultant|advisor))",
    re.I,
)
FOLLOW = re.compile(
    r"(?:please )?(?:"
    r"(?:can you )?call me (?:later|at (?P<date>\S+))|"
    r"(?:schedule|i (?:want|need|would like)) a follow[- ]up(?: at (?P<schedule>\S+))?)",
    re.I,
)
END = re.compile(
    r"(?:please )?(?:(?:end|stop) (?:the|this) (?:call|conversation)|"
    r"i (?:want|would like) to (?:end|stop) (?:the|this) (?:call|conversation))",
    re.I,
)


def normalized(text: str) -> str:
    return " ".join(text.strip().rstrip(".?!").split())


def validate_request(text: str, proposal: ActionProposal, now: datetime) -> datetime | None:
    # Requiring the whole short utterance prevents cherry-picked quoted/negated/conditional consent.
    if proposal.evidence != text.strip():
        raise WorkflowPolicyError("evidence must quote the complete current caller request")
    value = normalized(text)
    match = {"HUMAN_HANDOFF": HUMAN, "FOLLOW_UP": FOLLOW, "END_CONVERSATION": END}[
        proposal.action
    ].fullmatch(value)
    if match is None:
        raise WorkflowPolicyError("unsupported or ambiguous caller request")
    if proposal.action != "FOLLOW_UP":
        return None
    quoted_date = match.group("date") or match.group("schedule")
    if quoted_date:
        try:
            due = datetime.fromisoformat(quoted_date.replace("Z", "+00:00"))
        except ValueError:
            raise WorkflowPolicyError("schedule must be an ISO-8601 timestamp") from None
        if due.tzinfo is None or proposal.scheduled_at != due:
            raise WorkflowPolicyError("schedule must match the timezone-aware caller evidence")
    elif proposal.scheduled_at is not None:
        raise WorkflowPolicyError("the caller did not specify that schedule")
    else:
        due = now + timedelta(hours=24)
    due = due.astimezone(UTC)
    if not now < due <= now + timedelta(days=90):
        raise WorkflowPolicyError("follow-up must be in the next 90 days")
    return due


def acknowledgement(action: WorkflowAction, due: datetime | None) -> str:
    if action == "HUMAN_HANDOFF":
        return "Your request for human assistance has been recorded for a consultant to review."
    if action == "FOLLOW_UP":
        assert due is not None
        return (
            f"Your follow-up reminder is scheduled for {due.isoformat()}. "
            "This records a reminder, not an automatic phone call."
        )
    return "I will end this conversation now. Goodbye."


# This conservative screen supplements typed tools; it is not a general safety classifier.
UNSUPPORTED = re.compile(
    r"\b(?:guarantee\w*|eligible|eligibility|approved|approval|score|classification|"
    r"book\w*|schedul\w*|transfer\w*|handoff|hand[- ]off|follow[- ]up)\b",
    re.I,
)


def review_output(text: str) -> AgentOutputDecision:
    if UNSUPPORTED.search(text):
        return AgentOutputDecision(
            text=(
                "I can help collect your details. A consultant must review eligibility. "
                "Please make an explicit request if you want human assistance or a follow-up."
            ),
            allowed=False,
            reason="unsupported_claim",
        )
    return AgentOutputDecision(text=text, allowed=True, reason="allowed")

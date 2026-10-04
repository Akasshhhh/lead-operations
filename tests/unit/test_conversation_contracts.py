from uuid import uuid4

import pytest
from pydantic import ValidationError
from voice_platform_contracts.conversation import (
    ConversationTransition,
    ConversationTurn,
    TranscriptQuery,
)


def test_failure_and_turn_contracts_require_guard_fields() -> None:
    with pytest.raises(ValidationError, match="failure transitions require"):
        ConversationTransition(target_state="FAILED", expected_version=1)
    with pytest.raises(ValidationError, match="NUL"):
        ConversationTurn(
            turn_id=uuid4(), call_id=uuid4(), expected_version=1, user_text="bad\x00text"
        )


def test_turn_accepts_empty_facts_for_transcript_only_response() -> None:
    turn = ConversationTurn(
        turn_id=uuid4(), call_id=uuid4(), expected_version=1, user_text="I need more information"
    )
    assert turn.facts == []


def test_transcript_query_validates_cursors_and_nul_search() -> None:
    assert TranscriptQuery(query="  visa search  ").query == "visa search"
    with pytest.raises(ValidationError, match="mutually exclusive"):
        TranscriptQuery(before_sequence=4, after_sequence=2)
    with pytest.raises(ValidationError, match="NUL"):
        TranscriptQuery(query="visa\x00search")

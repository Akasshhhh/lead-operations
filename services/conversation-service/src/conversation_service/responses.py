"""Shared message-history representation for durable turn and workflow responses."""

from typing import Any, cast

from voice_platform_contracts.conversation import MessageHistoryEntry


def message_response(message: Any) -> MessageHistoryEntry:
    return MessageHistoryEntry(
        id=message.id,
        conversation_id=message.conversation_id,
        call_id=message.call_id,
        speaker=message.speaker,
        text=message.text,
        sequence_number=message.sequence_number,
        provider=message.provider,
        model=message.model,
        message_metadata=cast(dict[str, Any], message.message_metadata)
        if isinstance(message.message_metadata, dict)
        else {},
        turn_status=message.turn_status,
        qualification_error=message.qualification_error,
        redacted=message.redacted_at is not None,
        redacted_at=message.redacted_at,
        redaction_reason=message.redaction_reason,
        content_sha256=message.content_sha256,
        created_at=message.created_at,
    )

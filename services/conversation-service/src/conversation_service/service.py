"""Conversation state, call-session, and durable live-turn orchestration."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from voice_platform_contracts.conversation import (
    AgentMessageCreate,
    CallCreate,
    CallTransition,
    ConversationCreate,
    ConversationTransition,
    ConversationTurn,
    TranscriptQuery,
)
from voice_platform_contracts.lead import QualificationResponse
from voice_platform_db.models import Call, Conversation, DomainEvent, Message, TranscriptSegment

from .state_machine import (
    InvalidTransitionError,
    ensure_call_transition,
    ensure_conversation_transition,
)


class ConversationNotFoundError(LookupError):
    """Raised when a conversation or child record does not exist."""


class ActiveConversationError(ValueError):
    """Raised when a lead already has an active conversation."""


class ActiveCallError(ValueError):
    """Raised when a conversation already has an active call session."""


class ConversationVersionConflictError(ValueError):
    """Raised when a conversation version is stale."""


class CallVersionConflictError(ValueError):
    """Raised when a call-session version is stale."""


class TurnConflictError(ValueError):
    """Raised when a turn ID or pending-turn invariant is violated."""


class TurnCallNotConnectedError(ValueError):
    """Raised when a live turn is submitted without a connected call."""


class RetentionConfigurationError(ValueError):
    """Raised when an operator supplies an unsafe retention request."""


@dataclass(frozen=True, slots=True)
class TurnPersistence:
    conversation: Conversation
    call: Call
    message: Message


@dataclass(frozen=True, slots=True)
class HistoryPage:
    items: list[Message | TranscriptSegment]
    has_more: bool
    next_before_sequence: int | None
    next_after_sequence: int | None


@dataclass(frozen=True, slots=True)
class RetentionResult:
    messages_redacted: int
    segments_redacted: int


def action_for_state(state: str) -> str | None:
    return {
        "CREATED": "connect_call",
        "CONNECTING": "establish_call",
        "GREETING": "greet_user",
        "DISCOVERY": "continue_discovery",
        "QUALIFICATION": "ask_missing_qualification",
        "SCORING": "evaluate_qualification",
        "DECISION": "determine_next_action",
        "FOLLOW_UP": "schedule_follow_up",
        "HUMAN_HANDOFF": "request_human_handoff",
        "COMPLETED": None,
        "FAILED": None,
    }.get(state)


class ConversationService:
    """Transaction-neutral domain operations; callers own ``session.begin()``."""

    def __init__(self, session: AsyncSession, *, request_id: str | None = None) -> None:
        self.session = session
        self.request_id = request_id

    async def create_conversation(self, data: ConversationCreate) -> Conversation:
        existing = await self.session.scalar(
            select(Conversation).where(
                Conversation.lead_id == data.lead_id,
                Conversation.state.not_in(("COMPLETED", "FAILED")),
            )
        )
        if existing is not None:
            raise ActiveConversationError(str(data.lead_id))
        pending = await self.session.scalar(
            select(Message.id)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .where(Conversation.lead_id == data.lead_id, Message.turn_status == "PENDING")
            .limit(1)
        )
        if pending is not None:
            raise TurnConflictError("retry the pending turn before creating another conversation")
        conversation = Conversation(lead_id=data.lead_id, next_action=action_for_state("CREATED"))
        self.session.add(conversation)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            if "uq_conversation_one_active_per_lead" in str(exc.orig):
                raise ActiveConversationError(str(data.lead_id)) from exc
            raise
        self._event(
            event_type="conversation.created",
            aggregate_type="conversation",
            aggregate_id=conversation.id,
            aggregate_version=conversation.version,
            idempotency_key=f"conversation.created:{conversation.id}",
            payload={"conversation_id": str(conversation.id), "lead_id": str(data.lead_id)},
        )
        await self.session.flush()
        return conversation

    async def get_conversation(self, conversation_id: UUID, *, lock: bool = False) -> Conversation:
        statement = select(Conversation).where(Conversation.id == conversation_id)
        if lock:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        conversation = (await self.session.execute(statement)).scalar_one_or_none()
        if conversation is None:
            raise ConversationNotFoundError(str(conversation_id))
        return conversation

    async def transition_conversation(
        self, conversation_id: UUID, data: ConversationTransition
    ) -> Conversation:
        conversation = await self.get_conversation(conversation_id, lock=True)
        if conversation.version != data.expected_version:
            raise ConversationVersionConflictError(str(conversation_id))
        if data.target_state in {"SCORING", "DECISION"}:
            raise TurnConflictError("scoring and decision transitions are backend-orchestrated")
        try:
            ensure_conversation_transition(conversation.state, data.target_state)
        except InvalidTransitionError as exc:
            raise TurnConflictError(str(exc)) from exc
        self._set_conversation_state(conversation, data.target_state, data.reason, data.context)
        await self.session.flush()
        return conversation

    async def create_call(self, conversation_id: UUID, data: CallCreate) -> Call:
        conversation = await self.get_conversation(conversation_id, lock=True)
        if conversation.state in {"COMPLETED", "FAILED"}:
            raise TurnConflictError("cannot create a call for a terminal conversation")
        call = Call(
            conversation_id=conversation_id,
            transport=data.transport,
            runtime_instance_id=data.runtime_instance_id,
        )
        self.session.add(call)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            raise ActiveCallError(str(conversation_id)) from exc
        self._event(
            event_type="call.created",
            aggregate_type="call",
            aggregate_id=call.id,
            aggregate_version=call.version,
            idempotency_key=f"call.created:{call.id}",
            payload={"call_id": str(call.id), "conversation_id": str(conversation_id)},
        )
        await self.session.flush()
        return call

    async def get_call(self, conversation_id: UUID, call_id: UUID, *, lock: bool = False) -> Call:
        statement = select(Call).where(Call.id == call_id, Call.conversation_id == conversation_id)
        if lock:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        call = (await self.session.execute(statement)).scalar_one_or_none()
        if call is None:
            raise ConversationNotFoundError(str(call_id))
        return call

    async def transition_call(
        self, conversation_id: UUID, call_id: UUID, data: CallTransition
    ) -> Call:
        call = await self.get_call(conversation_id, call_id, lock=True)
        if call.version != data.expected_version:
            raise CallVersionConflictError(str(call_id))
        try:
            ensure_call_transition(call.status, data.target_status)
        except InvalidTransitionError as exc:
            raise TurnConflictError(str(exc)) from exc
        now = datetime.now(UTC)
        previous_status = call.status
        call.status = data.target_status
        call.version += 1
        if data.target_status == "CONNECTED":
            call.connected_at = call.connected_at or now
        elif data.target_status == "RECONNECTING":
            call.reconnect_attempts += 1
            call.disconnect_reason = data.reason
        elif data.target_status == "ENDED":
            call.ended_at = now
            call.disconnect_reason = data.reason
        elif data.target_status == "FAILED":
            call.failed_at = now
            call.ended_at = now
            call.failure_reason = data.reason
        payload: dict[str, object] = {
            "call_id": str(call.id),
            "conversation_id": str(conversation_id),
            "status": call.status,
        }
        if data.target_status == "FAILED":
            payload["failure"] = {
                "reason": data.reason,
                "from_status": previous_status,
                "context": {
                    "runtime_instance_id": call.runtime_instance_id,
                    "reconnect_attempts": call.reconnect_attempts,
                },
            }
        self._event(
            event_type="call.status_changed",
            aggregate_type="call",
            aggregate_id=call.id,
            aggregate_version=call.version,
            idempotency_key=f"call.status:{call.id}:{call.version}",
            payload=payload,
        )
        await self.session.flush()
        return call

    async def persist_turn(self, conversation_id: UUID, data: ConversationTurn) -> TurnPersistence:
        conversation = await self.get_conversation(conversation_id, lock=True)
        request_hash = self._turn_request_hash(data)
        existing = await self.session.get(
            Message, data.turn_id, with_for_update=True, populate_existing=True
        )
        if existing is not None:
            recorded = await self.session.scalar(
                select(DomainEvent).where(
                    DomainEvent.idempotency_key == f"conversation.turn.recorded:{data.turn_id}"
                )
            )
            payload = recorded.payload if recorded is not None else None
            stored_hash = payload.get("request_hash") if isinstance(payload, dict) else None
            # Older turns predate the fingerprint; retain their text/call replay check.
            matching_text = (
                existing.content_sha256 == self._content_hash(data.user_text)
                if existing.redacted_at is not None
                else existing.text == data.user_text
            )
            if (
                existing.conversation_id != conversation_id
                or existing.call_id != data.call_id
                or (stored_hash != request_hash if stored_hash else not matching_text)
            ):
                raise TurnConflictError(str(data.turn_id))
            call = await self.get_call(conversation_id, data.call_id, lock=True)
            return TurnPersistence(conversation=conversation, call=call, message=existing)
        if conversation.version != data.expected_version:
            raise ConversationVersionConflictError(str(conversation_id))
        if conversation.state not in {
            "GREETING",
            "DISCOVERY",
            "QUALIFICATION",
            "SCORING",
            "DECISION",
        }:
            raise TurnConflictError(f"turns are not accepted in {conversation.state}")
        call = await self.get_call(conversation_id, data.call_id, lock=True)
        if call.status != "CONNECTED":
            raise TurnCallNotConnectedError(str(data.call_id))
        pending = await self.session.scalar(
            select(Message.id)
            .where(Message.conversation_id == conversation_id, Message.turn_status == "PENDING")
            .limit(1)
        )
        if pending is not None:
            raise TurnConflictError("retry the pending turn before submitting another turn")

        max_sequence = await self.session.scalar(
            select(func.max(Message.sequence_number)).where(
                Message.conversation_id == conversation_id
            )
        )
        sequence = int(max_sequence or 0) + 1
        message = Message(
            id=data.turn_id,
            conversation_id=conversation_id,
            call_id=data.call_id,
            speaker="USER",
            text=data.user_text,
            sequence_number=sequence,
            provider="conversation-service",
            message_metadata={
                "qualification_facts": [fact.model_dump(mode="json") for fact in data.facts]
            },
        )
        self.session.add(message)
        self.session.add(
            TranscriptSegment(
                conversation_id=conversation_id,
                call_id=data.call_id,
                speaker="USER",
                text=data.user_text,
                segment_type="FINAL",
                sequence_number=sequence,
                is_final=True,
                provider="conversation-service",
                provider_segment_id=str(data.turn_id),
            )
        )
        conversation.last_turn_at = datetime.now(UTC)
        self._event(
            event_type="conversation.turn.recorded",
            aggregate_type="message",
            aggregate_id=data.turn_id,
            aggregate_version=1,
            idempotency_key=f"conversation.turn.recorded:{data.turn_id}",
            payload={
                "conversation_id": str(conversation_id),
                "call_id": str(data.call_id),
                "turn_id": str(data.turn_id),
                "sequence_number": sequence,
                "speaker": "USER",
                "request_hash": request_hash,
            },
        )
        await self.session.flush()
        return TurnPersistence(conversation=conversation, call=call, message=message)

    async def persist_agent_message(
        self, conversation_id: UUID, data: AgentMessageCreate
    ) -> Message:
        """Append generated text atomically; no qualification or state advancement."""
        conversation = await self.get_conversation(conversation_id, lock=True)
        payload = data.model_dump(mode="json", exclude={"expected_version"})
        request_hash = self._content_hash(json.dumps(payload, sort_keys=True))
        existing = await self.session.get(
            Message, data.message_id, with_for_update=True, populate_existing=True
        )
        if existing is not None:
            recorded = await self.session.scalar(
                select(DomainEvent).where(
                    DomainEvent.idempotency_key == f"conversation.agent.recorded:{data.message_id}"
                )
            )
            receipt = recorded.payload if recorded is not None else None
            if (
                existing.conversation_id != conversation_id
                or existing.call_id != data.call_id
                or existing.speaker != "AGENT"
                or not isinstance(receipt, dict)
                or receipt.get("request_hash") != request_hash
            ):
                raise TurnConflictError(str(data.message_id))
            return existing
        if conversation.version != data.expected_version:
            raise ConversationVersionConflictError(str(conversation_id))
        if conversation.state not in {
            "GREETING",
            "DISCOVERY",
            "QUALIFICATION",
            "SCORING",
            "DECISION",
        }:
            raise TurnConflictError("agent output requires an active conversation")
        call = await self.get_call(conversation_id, data.call_id, lock=True)
        if call.status != "CONNECTED":
            raise TurnCallNotConnectedError(str(data.call_id))
        pending = await self.session.scalar(
            select(Message.id)
            .where(Message.conversation_id == conversation_id, Message.turn_status == "PENDING")
            .limit(1)
        )
        if pending is not None:
            raise TurnConflictError("recover the pending user turn before recording agent output")
        if data.parent_turn_id is None:
            if conversation.state != "GREETING":
                raise TurnConflictError("agent replies require a parent user turn")
        else:
            latest = await self.session.scalar(
                select(Message)
                .where(Message.conversation_id == conversation_id, Message.speaker == "USER")
                .order_by(Message.sequence_number.desc())
                .limit(1)
            )
            if (
                latest is None
                or latest.id != data.parent_turn_id
                or latest.call_id != data.call_id
                or latest.turn_status != "APPLIED"
            ):
                raise TurnConflictError("agent reply requires the latest applied user turn")
        max_sequence = await self.session.scalar(
            select(func.max(Message.sequence_number)).where(
                Message.conversation_id == conversation_id
            )
        )
        sequence = int(max_sequence or 0) + 1
        metadata = {
            "output_kind": "generated",
            "parent_turn_id": str(data.parent_turn_id) if data.parent_turn_id else None,
        }
        message = Message(
            id=data.message_id,
            conversation_id=conversation_id,
            call_id=data.call_id,
            speaker="AGENT",
            text=data.text,
            sequence_number=sequence,
            provider=data.provider,
            model=data.model,
            turn_status="APPLIED",
            message_metadata=metadata,
        )
        self.session.add(message)
        self.session.add(
            TranscriptSegment(
                conversation_id=conversation_id,
                call_id=data.call_id,
                speaker="AGENT",
                text=data.text,
                segment_type="FINAL",
                sequence_number=sequence,
                is_final=True,
                provider=data.provider,
                provider_segment_id=str(data.message_id),
                segment_metadata=metadata,
            )
        )
        self._event(
            event_type="conversation.agent.recorded",
            aggregate_type="message",
            aggregate_id=data.message_id,
            aggregate_version=1,
            idempotency_key=f"conversation.agent.recorded:{data.message_id}",
            payload={
                "conversation_id": str(conversation_id),
                "call_id": str(data.call_id),
                "message_id": str(data.message_id),
                "sequence_number": sequence,
                "speaker": "AGENT",
                "request_hash": request_hash,
            },
        )
        await self.session.flush()
        return message

    async def mark_turn_pending(
        self, conversation_id: UUID, turn_id: UUID, error: Exception, *, rejected: bool = False
    ) -> Message:
        message = await self.session.get(
            Message, turn_id, with_for_update=True, populate_existing=True
        )
        if message is None or message.conversation_id != conversation_id:
            raise ConversationNotFoundError(str(turn_id))
        if message.turn_status in {"APPLIED", "FAILED"}:
            return message
        message.qualification_error = type(error).__name__
        # A definitive validation rejection cannot succeed by replaying unchanged input.
        message.turn_status = "FAILED" if rejected else "PENDING"
        await self.session.flush()
        return message

    async def finalize_turn(
        self,
        conversation_id: UUID,
        turn_id: UUID,
        qualification: QualificationResponse,
    ) -> Conversation:
        conversation = await self.get_conversation(conversation_id, lock=True)
        message = await self.session.get(
            Message, turn_id, with_for_update=True, populate_existing=True
        )
        if message is None or message.conversation_id != conversation_id:
            raise ConversationNotFoundError(str(turn_id))
        if message.turn_status == "APPLIED":
            return conversation

        target, next_action = self._orchestrated_target(conversation.state, qualification)
        if target is not None:
            self._set_conversation_state(conversation, target, None, {})
            if target == "SCORING":
                self._set_conversation_state(conversation, "DECISION", None, {})
                next_action = "determine_next_action"
        conversation.next_action = next_action
        conversation.last_turn_at = conversation.last_turn_at or datetime.now(UTC)
        message.turn_status = "APPLIED"
        message.qualification_error = None
        message.qualification_update_id = turn_id
        self._event(
            event_type="conversation.turn.applied",
            aggregate_type="message",
            aggregate_id=turn_id,
            aggregate_version=2,
            idempotency_key=f"conversation.turn.applied:{turn_id}",
            payload={
                "conversation_id": str(conversation_id),
                "turn_id": str(turn_id),
                "state": conversation.state,
                "next_action": next_action,
            },
        )
        await self.session.flush()
        return conversation

    async def active_call(self, conversation_id: UUID) -> Call | None:
        return await self.session.scalar(
            select(Call)
            .where(
                Call.conversation_id == conversation_id,
                Call.status.in_(("CREATED", "CONNECTING", "CONNECTED", "RECONNECTING")),
            )
            .order_by(Call.created_at.desc())
        )

    async def list_messages(self, conversation_id: UUID, query: TranscriptQuery) -> HistoryPage:
        await self.get_conversation(conversation_id)
        statement = select(Message).where(Message.conversation_id == conversation_id)
        if query.speaker is not None:
            statement = statement.where(Message.speaker == query.speaker)
        if query.query is not None:
            statement = statement.where(
                Message.redacted_at.is_(None),
                Message.text.ilike(self._search_pattern(query.query), escape="\\"),
            )
        return await self._history_page(statement, Message.sequence_number, query)

    async def list_transcript_segments(
        self, conversation_id: UUID, query: TranscriptQuery
    ) -> HistoryPage:
        await self.get_conversation(conversation_id)
        statement = select(TranscriptSegment).where(
            TranscriptSegment.conversation_id == conversation_id
        )
        if query.speaker is not None:
            statement = statement.where(TranscriptSegment.speaker == query.speaker)
        if query.query is not None:
            statement = statement.where(
                TranscriptSegment.redacted_at.is_(None),
                TranscriptSegment.text.ilike(self._search_pattern(query.query), escape="\\"),
            )
        return await self._history_page(statement, TranscriptSegment.sequence_number, query)

    async def redact_expired_content(
        self,
        *,
        cutoff: datetime,
        batch_size: int = 500,
        reason: str = "retention",
        dry_run: bool = False,
    ) -> RetentionResult:
        if cutoff.tzinfo is None:
            raise RetentionConfigurationError("cutoff must include a timezone")
        if not 1 <= batch_size <= 1_000:
            raise RetentionConfigurationError("batch_size must be between 1 and 1000")
        if not 1 <= len(reason) <= 80:
            raise RetentionConfigurationError("reason must be between 1 and 80 characters")
        terminal_conversations = select(Conversation.id).where(
            Conversation.state.in_(("COMPLETED", "FAILED")),
            Conversation.completed_at.is_not(None),
            Conversation.completed_at <= cutoff,
            ~select(Message.id)
            .where(Message.conversation_id == Conversation.id, Message.turn_status == "PENDING")
            .exists(),
        )
        message_statement = (
            select(Message)
            .where(
                Message.conversation_id.in_(terminal_conversations),
                Message.redacted_at.is_(None),
            )
            .order_by(Message.created_at, Message.id)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        segment_statement = (
            select(TranscriptSegment)
            .where(
                TranscriptSegment.conversation_id.in_(terminal_conversations),
                TranscriptSegment.redacted_at.is_(None),
            )
            .order_by(TranscriptSegment.created_at, TranscriptSegment.id)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        if dry_run:
            message_count = await self.session.scalar(
                select(func.count())
                .select_from(Message)
                .where(
                    Message.conversation_id.in_(terminal_conversations),
                    Message.redacted_at.is_(None),
                )
            )
            segment_count = await self.session.scalar(
                select(func.count())
                .select_from(TranscriptSegment)
                .where(
                    TranscriptSegment.conversation_id.in_(terminal_conversations),
                    TranscriptSegment.redacted_at.is_(None),
                )
            )
            return RetentionResult(int(message_count or 0), int(segment_count or 0))
        messages = list((await self.session.execute(message_statement)).scalars().all())
        segments = list((await self.session.execute(segment_statement)).scalars().all())
        redacted_at = datetime.now(UTC)
        for message in messages:
            message.content_sha256 = self._content_hash(message.text)
            message.text = "[REDACTED]"
            message.message_metadata = {}
            message.redacted_at = redacted_at
            message.redaction_reason = reason
        for segment in segments:
            segment.content_sha256 = self._content_hash(segment.text)
            segment.text = "[REDACTED]"
            segment.segment_metadata = {}
            segment.redacted_at = redacted_at
            segment.redaction_reason = reason
        await self.session.flush()
        return RetentionResult(len(messages), len(segments))

    async def _history_page(
        self, statement: Any, sequence_column: Any, query: TranscriptQuery
    ) -> HistoryPage:
        descending = query.after_sequence is None
        if query.before_sequence is not None:
            statement = statement.where(sequence_column < query.before_sequence).order_by(
                sequence_column.desc()
            )
        elif query.after_sequence is not None:
            descending = False
            statement = statement.where(sequence_column > query.after_sequence).order_by(
                sequence_column.asc()
            )
        else:
            statement = statement.order_by(sequence_column.desc())
        rows: list[Message | TranscriptSegment] = list(
            (await self.session.execute(statement.limit(query.limit + 1))).scalars().all()
        )
        has_more = len(rows) > query.limit
        rows = rows[: query.limit]
        if descending:
            rows.reverse()
        next_before = rows[0].sequence_number if has_more and descending and rows else None
        next_after = rows[-1].sequence_number if has_more and not descending and rows else None
        return HistoryPage(rows, has_more, next_before, next_after)

    @staticmethod
    def _search_pattern(query: str) -> str:
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return f"%{escaped}%"

    @staticmethod
    def _content_hash(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _turn_request_hash(data: ConversationTurn) -> str:
        # The expected version is an admission guard, not the identity of a replay.
        payload = {
            "call_id": str(data.call_id),
            "user_text": data.user_text,
            "facts": [
                fact.model_dump(mode="json")
                for fact in sorted(data.facts, key=lambda f: f.field_key)
            ],
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def _orchestrated_target(
        self, current: str, qualification: QualificationResponse
    ) -> tuple[str | None, str | None]:
        contradictory = qualification.status == "CONTRADICTORY"
        complete = qualification.completeness == 100 and not contradictory
        if current == "GREETING":
            return "DISCOVERY", "continue_discovery"
        if current == "DISCOVERY":
            return (
                ("DISCOVERY", "clarify_contradiction")
                if contradictory
                else ("QUALIFICATION", "ask_missing_qualification")
            )
        if current == "QUALIFICATION":
            if contradictory:
                return "DISCOVERY", "clarify_contradiction"
            if complete:
                return "SCORING", "evaluate_qualification"
            return None, "ask_missing_qualification"
        if current == "SCORING":
            return (
                ("QUALIFICATION", "clarify_contradiction")
                if contradictory
                else ("DECISION", "determine_next_action")
            )
        if current == "DECISION":
            flags = {answer.field_key: answer for answer in qualification.answers}
            if (
                flags.get("human_requested")
                and flags["human_requested"].value is True
                and flags["human_requested"].answer_status == "CONFIRMED"
            ):
                return "HUMAN_HANDOFF", "request_human_handoff"
            if (
                flags.get("follow_up_required")
                and flags["follow_up_required"].value is True
                and flags["follow_up_required"].answer_status == "CONFIRMED"
            ):
                return "FOLLOW_UP", "schedule_follow_up"
            return None, "complete_or_route"
        return None, action_for_state(current)

    def _set_conversation_state(
        self,
        conversation: Conversation,
        target: str,
        reason: str | None,
        context: Mapping[str, object],
    ) -> None:
        current = conversation.state
        if current != target:
            try:
                ensure_conversation_transition(current, target)
            except InvalidTransitionError as exc:
                raise TurnConflictError(str(exc)) from exc
            conversation.state = target
            conversation.version += 1
        if target == "CONNECTING" and conversation.started_at is None:
            conversation.started_at = datetime.now(UTC)
        if target in {"COMPLETED", "FAILED"}:
            conversation.completed_at = conversation.completed_at or datetime.now(UTC)
        if target == "FAILED":
            conversation.failure_reason = reason
            conversation.failure_context = dict(context)
        conversation.next_action = action_for_state(target)
        if current != target:
            payload: dict[str, object] = {
                "conversation_id": str(conversation.id),
                "from_state": current,
                "to_state": target,
                "reason": reason,
            }
            if target == "FAILED":
                payload["failure"] = {
                    "reason": reason,
                    "from_state": current,
                    "context": dict(context),
                }
            self._event(
                event_type="conversation.state_changed",
                aggregate_type="conversation",
                aggregate_id=conversation.id,
                aggregate_version=conversation.version,
                idempotency_key=f"conversation.state:{conversation.id}:{conversation.version}",
                payload=payload,
            )

    def _event(
        self,
        *,
        event_type: str,
        aggregate_type: str,
        aggregate_id: UUID,
        aggregate_version: int,
        idempotency_key: str,
        payload: dict[str, object],
    ) -> None:
        self.session.add(
            DomainEvent(
                event_id=uuid5(NAMESPACE_URL, f"voice-ai-platform:{idempotency_key}"),
                event_type=event_type,
                producer="conversation-service",
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                aggregate_version=aggregate_version,
                request_id=self.request_id,
                idempotency_key=idempotency_key,
                payload=payload,
                occurred_at=datetime.now(UTC),
            )
        )

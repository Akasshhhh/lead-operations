"""Conversation-owned caller actions and bounded durable reminder dispatch."""

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import and_, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from voice_platform_contracts.conversation import AgentMessageCreate, ConversationResponse
from voice_platform_contracts.workflow import (
    AgentOutputCheck,
    AgentOutputDecision,
    FollowUpDispatch,
    FollowUpResponse,
    FollowUpTransition,
    HandoffResponse,
    HandoffTransition,
    WorkflowAction,
    WorkflowActionCreate,
    WorkflowActionResponse,
    WorkflowSnapshot,
)
from voice_platform_db.models import DomainEvent, FollowUp, FollowUpAttempt, Handoff, Message

from .policy import acknowledgement, review_output, validate_request
from .responses import message_response
from .service import ConversationNotFoundError, ConversationService, TurnConflictError


def fingerprint(data: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            data.model_dump(mode="json", exclude={"expected_version"}), sort_keys=True
        ).encode()
    ).hexdigest()


def metadata(value: object) -> dict[str, Any]:
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


class WorkflowService(ConversationService):
    async def _receipt(self, key: str, request_hash: str, cid: UUID) -> DomainEvent | None:
        # Serialize globally scoped operation IDs even when callers supply different conversations.
        lock_id = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], signed=True)
        await self.session.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": lock_id})
        event = await self.session.scalar(
            select(DomainEvent).where(DomainEvent.idempotency_key == key)
        )
        if event is not None:
            payload = metadata(event.payload)
            if payload.get("request_hash") != request_hash or payload.get("conversation_id") != str(
                cid
            ):
                raise TurnConflictError("workflow operation identity or payload changed")
        return event

    async def caller_turn(self, cid: UUID, tid: UUID, call_id: UUID) -> Message:
        message = await self.session.get(Message, tid, populate_existing=True)
        latest = await self.session.scalar(
            select(Message.id)
            .where(Message.conversation_id == cid, Message.speaker == "USER")
            .order_by(Message.sequence_number.desc())
            .limit(1)
        )
        pending = await self.session.scalar(
            select(Message.id)
            .where(Message.conversation_id == cid, Message.turn_status == "PENDING")
            .limit(1)
        )
        if (
            message is None
            or message.conversation_id != cid
            or message.call_id != call_id
            or message.speaker != "USER"
            or message.turn_status != "APPLIED"
            or message.redacted_at is not None
            or latest != tid
            or pending is not None
        ):
            raise TurnConflictError("action requires the latest unredacted applied caller turn")
        call = await self.get_call(cid, call_id, lock=True)
        if call.status != "CONNECTED":
            raise TurnConflictError("action requires a connected owning call")
        return message

    async def execute(self, cid: UUID, data: WorkflowActionCreate) -> WorkflowActionResponse:
        current = await self.get_conversation(cid, lock=True)
        request_hash = fingerprint(data)
        key = f"workflow.action:{data.action_id}"
        existing = await self._receipt(key, request_hash, cid)
        mid = uuid5(NAMESPACE_URL, f"workflow-ack:{data.action_id}")
        if existing is None:
            if current.version != data.expected_version:
                raise TurnConflictError("stale workflow admission version")
            if current.state not in {
                "GREETING",
                "DISCOVERY",
                "QUALIFICATION",
                "SCORING",
                "DECISION",
            }:
                raise TurnConflictError("caller actions require an active conversational state")
            turn = await self.caller_turn(cid, data.turn_id, data.call_id)
            consumed = await self.session.scalar(
                select(Message.id)
                .where(
                    Message.conversation_id == cid,
                    Message.speaker == "AGENT",
                    Message.message_metadata["parent_turn_id"].astext == str(data.turn_id),
                )
                .limit(1)
            )
            if consumed is not None:
                raise TurnConflictError("the caller turn already has an agent reply")
            now = datetime.now(UTC)
            due = validate_request(turn.text, data, now)
            from_state = current.state
            if (
                await self.session.get(Handoff, data.action_id) is not None
                or await self.session.get(FollowUp, data.action_id) is not None
            ):
                raise TurnConflictError("workflow ID already exists")
            # This flush precedes workflow insertion and every business-state change.
            recorded_ack = await self.persist_agent_message(
                cid,
                AgentMessageCreate(
                    message_id=mid,
                    call_id=data.call_id,
                    expected_version=current.version,
                    parent_turn_id=data.turn_id,
                    text=acknowledgement(data.action, due),
                    provider="voice-runtime",
                    model="workflow-policy-v1",
                ),
            )
            recorded_ack.message_metadata = metadata(recorded_ack.message_metadata) | {
                "workflow_action_id": str(data.action_id),
                "policy_version": "caller-request-v1",
            }
            if data.action == "HUMAN_HANDOFF":
                self.session.add(
                    Handoff(
                        id=data.action_id,
                        lead_id=current.lead_id,
                        conversation_id=cid,
                        reason="explicit_caller_request",
                        summary="Caller requested human assistance.",
                        metadata_json={
                            "turn_id": str(data.turn_id),
                            "policy_version": "caller-request-v1",
                        },
                    )
                )
            elif data.action == "FOLLOW_UP":
                assert due is not None
                self.session.add(
                    FollowUp(
                        id=data.action_id,
                        lead_id=current.lead_id,
                        conversation_id=cid,
                        followup_type="CONSULTANT_REMINDER",
                        scheduled_at=due,
                        idempotency_key=key,
                        reason="explicit_caller_request",
                        metadata_json={
                            "turn_id": str(data.turn_id),
                            "policy_version": "caller-request-v1",
                        },
                    )
                )
            await self.session.flush()
            # Approved exception: explicit caller routing traverses existing graph edges.
            # SCORING is only a routing waypoint here; no qualification/score result is invented.
            path = {
                "GREETING": ["DISCOVERY", "QUALIFICATION", "SCORING", "DECISION"],
                "DISCOVERY": ["QUALIFICATION", "SCORING", "DECISION"],
                "QUALIFICATION": ["SCORING", "DECISION"],
                "SCORING": ["DECISION"],
                "DECISION": [],
            }[current.state]
            target = "COMPLETED" if data.action == "END_CONVERSATION" else data.action
            for state in [*path, target]:
                self._set_conversation_state(current, state, "explicit_caller_request", {})
            current.next_action = {
                "HUMAN_HANDOFF": "await_human_review",
                "FOLLOW_UP": "await_follow_up",
                "END_CONVERSATION": None,
            }[data.action]
            self._event(
                event_type="workflow.action.applied",
                aggregate_type="workflow_action",
                aggregate_id=data.action_id,
                aggregate_version=1,
                idempotency_key=key,
                payload={
                    "conversation_id": str(cid),
                    "action": data.action,
                    "turn_id": str(data.turn_id),
                    "acknowledgement_id": str(mid),
                    "request_hash": request_hash,
                    "policy_version": "caller-request-v1",
                    "early_routing": bool(path),
                    "from_state": from_state,
                },
            )
            await self.session.flush()
        return await self.result(cid, data.action_id)

    async def result(self, cid: UUID, action_id: UUID) -> WorkflowActionResponse:
        current = await self.get_conversation(cid)
        receipt = await self.session.scalar(
            select(DomainEvent).where(DomainEvent.idempotency_key == f"workflow.action:{action_id}")
        )
        if receipt is None or metadata(receipt.payload).get("conversation_id") != str(cid):
            raise ConversationNotFoundError(str(action_id))
        action = cast(WorkflowAction, metadata(receipt.payload)["action"])
        mid = uuid5(NAMESPACE_URL, f"workflow-ack:{action_id}")
        ack = await self.session.get(Message, mid)
        if ack is None:
            raise TurnConflictError("workflow acknowledgement unavailable")
        handoff = await self.session.get(Handoff, action_id) if action == "HUMAN_HANDOFF" else None
        follow = await self.session.get(FollowUp, action_id) if action == "FOLLOW_UP" else None
        return WorkflowActionResponse(
            action_id=action_id,
            action=action,
            conversation=ConversationResponse.model_validate(current),
            acknowledgement=message_response(ack),
            handoff=HandoffResponse.model_validate(handoff) if handoff else None,
            follow_up=FollowUpResponse.model_validate(follow) if follow else None,
        )

    async def output_check(self, cid: UUID, data: AgentOutputCheck) -> AgentOutputDecision:
        await self.get_conversation(cid, lock=True)
        await self.caller_turn(cid, data.turn_id, data.call_id)
        return review_output(data.text)

    async def snapshot(self, cid: UUID) -> WorkflowSnapshot:
        await self.get_conversation(cid)
        handoffs = await self.session.scalars(
            select(Handoff)
            .where(Handoff.conversation_id == cid)
            .order_by(Handoff.requested_at.desc())
            .limit(100)
        )
        follows = await self.session.scalars(
            select(FollowUp)
            .where(FollowUp.conversation_id == cid)
            .order_by(FollowUp.created_at.desc())
            .limit(100)
        )
        return WorkflowSnapshot(
            handoffs=[HandoffResponse.model_validate(h) for h in handoffs],
            follow_ups=[FollowUpResponse.model_validate(f) for f in follows],
        )

    async def transition(
        self, cid: UUID, wid: UUID, data: HandoffTransition | FollowUpTransition
    ) -> HandoffResponse | FollowUpResponse:
        current = await self.get_conversation(cid, lock=True)
        handoff = isinstance(data, HandoffTransition)
        row: Handoff | FollowUp | None
        if handoff:
            row = await self.session.get(Handoff, wid, with_for_update=True, populate_existing=True)
        else:
            row = await self.session.get(
                FollowUp, wid, with_for_update=True, populate_existing=True
            )
        if row is None or row.conversation_id != cid:
            raise ConversationNotFoundError(str(wid))
        key = f"workflow.operation:{data.operation_id}"
        request_hash = hashlib.sha256((str(wid) + fingerprint(data)).encode()).hexdigest()
        receipt = await self._receipt(key, request_hash, cid)
        if receipt is None:
            allowed = (
                {"REQUESTED": {"ASSIGNED", "CANCELLED"}, "ASSIGNED": {"COMPLETED", "CANCELLED"}}
                if handoff
                else {
                    "SCHEDULED": {"CANCELLED"},
                    "READY": {"COMPLETED", "CANCELLED"},
                    "FAILED": {"SCHEDULED", "CANCELLED"},
                }
            )
            if row.status != data.expected_status or data.target_status not in allowed.get(
                row.status, set()
            ):
                raise TurnConflictError("invalid or stale workflow transition")
            row.status = data.target_status
            now = datetime.now(UTC)
            if isinstance(row, Handoff):
                if row.status == "ASSIGNED":
                    row.assigned_at = now
                elif row.status in {"COMPLETED", "CANCELLED"}:
                    row.completed_at = now
            elif row.status == "SCHEDULED":
                row.max_attempts = row.attempt + 3
                row.scheduled_at = now
                row.last_error = None
            if row.status in {"COMPLETED", "CANCELLED"} and current.state == (
                "HUMAN_HANDOFF" if handoff else "FOLLOW_UP"
            ):
                self._set_conversation_state(current, "COMPLETED", "workflow_finished", {})
            self._event(
                event_type="workflow.status_changed",
                aggregate_type="workflow_operation",
                aggregate_id=data.operation_id,
                aggregate_version=1,
                idempotency_key=key,
                payload={
                    "conversation_id": str(cid),
                    "workflow_id": str(wid),
                    "status": row.status,
                    "request_hash": request_hash,
                },
            )
            await self.session.flush()
        return (
            HandoffResponse.model_validate(row) if handoff else FollowUpResponse.model_validate(row)
        )


async def reminder_result(_: UUID) -> dict[str, object]:
    """Local demo effect: make the durable reminder ready for dashboard review."""
    return {"kind": "dashboard_reminder", "external_contact": False}


async def dispatch_followups(
    sessions: async_sessionmaker[AsyncSession],
    *,
    batch_size: int = 50,
    execute: Callable[[UUID], Awaitable[dict[str, object]]] = reminder_result,
) -> FollowUpDispatch:
    if not 1 <= batch_size <= 100:
        raise ValueError("batch_size must be between 1 and 100")
    now = datetime.now(UTC)
    claims: list[tuple[UUID, int, str]] = []
    result = FollowUpDispatch()
    async with sessions.begin() as db:
        rows = await db.scalars(
            select(FollowUp)
            .where(
                FollowUp.followup_type == "CONSULTANT_REMINDER",
                or_(
                    FollowUp.status == "SCHEDULED",
                    and_(
                        FollowUp.status == "RUNNING",
                        or_(
                            FollowUp.metadata_json["lease_until"].astext.is_(None),
                            FollowUp.metadata_json["lease_until"].astext <= now.isoformat(),
                        ),
                    ),
                ),
                FollowUp.scheduled_at <= now,
            )
            .order_by(FollowUp.scheduled_at, FollowUp.id)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        for row in rows:
            meta = metadata(row.metadata_json)
            if row.status == "RUNNING":
                lease = meta.get("lease_until")
                if isinstance(lease, str) and datetime.fromisoformat(lease) > now:
                    continue
                previous = await db.scalar(
                    select(FollowUpAttempt).where(
                        FollowUpAttempt.followup_id == row.id,
                        FollowUpAttempt.attempt_number == row.attempt,
                    )
                )
                if previous is not None:
                    previous.status = "FAILED"
                    previous.completed_at = now
                    previous.error = "lease_expired"
                    WorkflowService(db)._event(
                        event_type="follow_up.attempt_finished",
                        aggregate_type="follow_up_attempt",
                        aggregate_id=previous.id,
                        aggregate_version=2,
                        idempotency_key=f"follow-up.attempt:{previous.id}",
                        payload={
                            "follow_up_id": str(row.id),
                            "conversation_id": str(row.conversation_id),
                            "attempt": row.attempt,
                            "status": "FAILED",
                            "reason": "lease_expired",
                        },
                    )
            if row.attempt >= row.max_attempts:
                row.status = "FAILED"
                row.last_error = "attempts_exhausted"
                result.failed += 1
                continue
            token = str(uuid4())
            row.attempt += 1
            row.status = "RUNNING"
            row.metadata_json = meta | {
                "lease_token": token,
                "lease_until": (now + timedelta(seconds=batch_size * 10 + 60)).isoformat(),
            }
            attempt = FollowUpAttempt(followup_id=row.id, attempt_number=row.attempt)
            db.add(attempt)
            await db.flush()
            WorkflowService(db)._event(
                event_type="follow_up.attempt_started",
                aggregate_type="follow_up_attempt",
                aggregate_id=attempt.id,
                aggregate_version=1,
                idempotency_key=f"follow-up.attempt.started:{attempt.id}",
                payload={
                    "follow_up_id": str(row.id),
                    "conversation_id": str(row.conversation_id),
                    "attempt": row.attempt,
                    "status": "RUNNING",
                },
            )
            claims.append((row.id, row.attempt, token))
    result.claimed = len(claims)
    for wid, number, token in claims:
        error: str | None = None
        try:
            async with asyncio.timeout(10):
                await execute(wid)
        except Exception:
            error = "reminder_dispatch_failed"
        async with sessions.begin() as db:
            claimed_row = await db.get(FollowUp, wid, with_for_update=True, populate_existing=True)
            assert claimed_row is not None
            row = claimed_row
            if row.status != "RUNNING" or metadata(row.metadata_json).get("lease_token") != token:
                result.stale += 1
                continue
            finished_attempt = await db.scalar(
                select(FollowUpAttempt).where(
                    FollowUpAttempt.followup_id == wid, FollowUpAttempt.attempt_number == number
                )
            )
            assert finished_attempt is not None
            attempt = finished_attempt
            attempt.completed_at = datetime.now(UTC)
            attempt.status = "FAILED" if error else "COMPLETED"
            attempt.error = error
            # Persist only the bounded local effect, not arbitrary handler data.
            attempt.result = (
                {"kind": "dashboard_reminder", "external_contact": False} if not error else {}
            )
            row.last_error = error
            row.status = (
                "READY" if not error else ("FAILED" if number >= row.max_attempts else "SCHEDULED")
            )
            if error:
                row.scheduled_at = datetime.now(UTC) + timedelta(seconds=min(300, 2**number))
                result.failed += 1
            else:
                result.ready += 1
            service = WorkflowService(db)
            service._event(
                event_type="follow_up.attempt_finished",
                aggregate_type="follow_up_attempt",
                aggregate_id=attempt.id,
                aggregate_version=2,
                idempotency_key=f"follow-up.attempt:{attempt.id}",
                payload={
                    "follow_up_id": str(wid),
                    "conversation_id": str(row.conversation_id),
                    "attempt": number,
                    "status": row.status,
                },
            )
    return result

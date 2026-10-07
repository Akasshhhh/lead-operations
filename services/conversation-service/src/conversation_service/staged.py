"""Additive staged turn operations on existing records; caller owns each transaction."""

import json
from uuid import UUID

from sqlalchemy import select
from voice_platform_contracts.qualification import (
    ProposalRejection,
    ProposedFacts,
    StagedTurnCreate,
    ValidatedFacts,
)
from voice_platform_db.models import DomainEvent, Message

from .service import ConversationService, TurnConflictError


class StagedTurns(ConversationService):
    @staticmethod
    def input_hash(data: StagedTurnCreate) -> str:
        # Namespace separates staged input from the immutable legacy /turns receipt.
        return "staged:" + ConversationService._content_hash(
            json.dumps(data.model_dump(mode="json", exclude={"expected_version"}), sort_keys=True)
        )

    @staticmethod
    def proposal_hash(data: ProposedFacts) -> str:
        # Default requests retain their historical receipt hash.
        payload = data.model_dump(mode="json", exclude={"on_rejection", "extraction_rejection"})
        if data.on_rejection != "reject":
            payload["on_rejection"] = data.on_rejection
        if data.extraction_rejection is not None:
            payload["extraction_rejection"] = data.extraction_rejection.model_dump(mode="json")
        payload["proposals"] = sorted(payload["proposals"], key=lambda p: p["field_key"])
        return ConversationService._content_hash(json.dumps(payload, sort_keys=True))

    async def input(self, cid: UUID, data: StagedTurnCreate) -> Message:
        await self.get_conversation(cid, lock=True)
        existing = await self.session.get(
            Message, data.turn_id, with_for_update=True, populate_existing=True
        )
        if existing is not None:
            await self.message(cid, data.turn_id)
            event = await self.session.scalar(
                select(DomainEvent).where(
                    DomainEvent.idempotency_key == f"conversation.turn.recorded:{data.turn_id}"
                )
            )
            if (
                event is None
                or not isinstance(event.payload, dict)
                or event.payload.get("request_hash") != self.input_hash(data)
            ):
                raise TurnConflictError("staged input identity conflict")
            return existing
        persisted = await self.persist_turn(cid, data.as_turn())
        event = await self.session.scalar(
            select(DomainEvent).where(
                DomainEvent.idempotency_key == f"conversation.turn.recorded:{data.turn_id}"
            )
        )
        assert event is not None and isinstance(event.payload, dict)
        # This is still inside the initial transaction, before relay visibility.
        event.payload = event.payload | {"request_hash": self.input_hash(data), "staged": True}
        persisted.message.message_metadata = {"stage": "RECORDED", "qualification_facts": []}
        await self.session.flush()
        return persisted.message

    async def message(self, cid: UUID, tid: UUID) -> Message:
        await self.get_conversation(cid, lock=True)
        message = await self.session.get(Message, tid, with_for_update=True, populate_existing=True)
        receipt = await self.session.scalar(
            select(DomainEvent).where(
                DomainEvent.idempotency_key == f"conversation.turn.recorded:{tid}"
            )
        )
        if (
            message is None
            or message.conversation_id != cid
            or message.speaker != "USER"
            or receipt is None
            or not isinstance(receipt.payload, dict)
            or receipt.payload.get("staged") is not True
        ):
            raise TurnConflictError("not a staged user turn in this conversation")
        return message

    async def bound(self, cid: UUID, tid: UUID, data: ProposedFacts) -> Message | None:
        message = await self.message(cid, tid)
        receipt = await self.session.scalar(
            select(DomainEvent).where(
                DomainEvent.idempotency_key == f"conversation.turn.facts_bound:{tid}"
            )
        )
        if receipt is not None:
            if not isinstance(receipt.payload, dict) or receipt.payload.get(
                "request_hash"
            ) != self.proposal_hash(data):
                raise TurnConflictError("staged facts are immutable")
            return message
        if message.turn_status != "PENDING" or message.redacted_at is not None:
            raise TurnConflictError("turn cannot accept facts")
        return None

    async def bind(
        self,
        cid: UUID,
        tid: UUID,
        data: ProposedFacts,
        validated: ValidatedFacts,
        rejection: ProposalRejection | None = None,
    ) -> Message:
        existing = await self.bound(cid, tid, data)
        if existing is not None:
            return existing
        message = await self.message(cid, tid)
        message.message_metadata = {
            "stage": "BOUND",
            "call_provenance": "call-v1",
            "qualification_facts": [fact.model_dump(mode="json") for fact in validated.facts],
            "provenance": [
                item
                | {
                    "turn_id": str(tid),
                    "call_id": str(message.call_id),
                    "source": "USER_TRANSCRIPT",
                    "validation_policy": "evidence-v1",
                }
                for item in validated.provenance
            ],
            "proposal_hash": self.proposal_hash(data),
        } | (
            {
                "proposal_rejection": rejection.model_dump(mode="json")
                | {"provider": data.provider, "model": data.model}
            }
            if rejection
            else {}
        )
        message.qualification_error = None
        self._event(
            event_type="conversation.turn.facts_bound",
            aggregate_type="turn_facts",
            aggregate_id=tid,
            aggregate_version=1,
            idempotency_key=f"conversation.turn.facts_bound:{tid}",
            payload={
                "conversation_id": str(cid),
                "turn_id": str(tid),
                "request_hash": self.proposal_hash(data),
            },
        )
        await self.session.flush()
        return message

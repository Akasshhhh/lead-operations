"""Conversation-owned discovery/call/event reads independent of Lead and Redis."""

from uuid import UUID

from sqlalchemy import func, or_, select
from voice_platform_contracts.conversation import CallResponse, ConversationResponse
from voice_platform_contracts.dashboard import (
    CallListResponse,
    ConversationEvent,
    ConversationEventsResponse,
    ConversationListResponse,
)
from voice_platform_db.models import Call, Conversation, DomainEvent

from .service import ConversationService


class DashboardReads(ConversationService):
    async def conversations(
        self, lead_id: UUID, active_only: bool, limit: int, offset: int
    ) -> ConversationListResponse:
        conditions = [Conversation.lead_id == lead_id]
        if active_only:
            conditions.append(Conversation.state.not_in(("COMPLETED", "FAILED")))
        total = await self.session.scalar(
            select(func.count()).select_from(Conversation).where(*conditions)
        )
        rows = await self.session.scalars(
            select(Conversation)
            .where(*conditions)
            .order_by(Conversation.created_at.desc(), Conversation.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return ConversationListResponse(
            items=[ConversationResponse.model_validate(row) for row in rows],
            total=int(total or 0),
            limit=limit,
            offset=offset,
        )

    async def calls(self, cid: UUID, limit: int, offset: int) -> CallListResponse:
        await self.get_conversation(cid)
        total = await self.session.scalar(
            select(func.count()).select_from(Call).where(Call.conversation_id == cid)
        )
        rows = await self.session.scalars(
            select(Call)
            .where(Call.conversation_id == cid)
            .order_by(Call.created_at.desc(), Call.id.desc())
            .limit(limit)
            .offset(offset)
        )
        active = await self.active_call(cid)
        return CallListResponse(
            items=[CallResponse.model_validate(row) for row in rows],
            active_call=CallResponse.model_validate(active) if active else None,
            total=int(total or 0),
            limit=limit,
            offset=offset,
        )

    async def events(self, cid: UUID, limit: int, offset: int) -> ConversationEventsResponse:
        await self.get_conversation(cid)
        conditions = [
            DomainEvent.producer == "conversation-service",
            or_(
                DomainEvent.aggregate_id == cid,
                DomainEvent.payload["conversation_id"].astext == str(cid),
            ),
        ]
        total = await self.session.scalar(
            select(func.count()).select_from(DomainEvent).where(*conditions)
        )
        rows = await self.session.scalars(
            select(DomainEvent)
            .where(*conditions)
            .order_by(DomainEvent.occurred_at.desc(), DomainEvent.event_id.desc())
            .limit(limit)
            .offset(offset)
        )
        return ConversationEventsResponse(
            items=[
                ConversationEvent(
                    event_id=row.event_id,
                    event_type=row.event_type,
                    producer=row.producer,
                    aggregate_type=row.aggregate_type,
                    aggregate_id=row.aggregate_id,
                    aggregate_version=row.aggregate_version,
                    occurred_at=row.occurred_at,
                    published_at=row.published_at,
                    publish_attempts=row.publish_attempts,
                )
                for row in rows
            ],
            total=int(total or 0),
            limit=limit,
            offset=offset,
        )

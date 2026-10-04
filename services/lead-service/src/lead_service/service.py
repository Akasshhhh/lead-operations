"""Lead domain operations and persistence orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from voice_platform_db.models import (
    DomainEvent,
    Lead,
    QualificationAnswer,
    QualificationProfile,
)

from .schemas import LeadCreate, LeadUpdate


class LeadNotFoundError(LookupError):
    """Raised when a requested lead does not exist."""


class DuplicateLeadError(ValueError):
    """Raised when a synthetic profile key is already in use."""


class LeadVersionConflictError(ValueError):
    """Raised when an optimistic update uses a stale lead version."""


@dataclass(frozen=True, slots=True)
class LeadList:
    items: list[Lead]
    total: int


@dataclass(frozen=True, slots=True)
class QualificationData:
    profile: QualificationProfile
    answers: list[QualificationAnswer]


class LeadService:
    """Lead operations within a caller-owned transaction; never commits or retries."""

    def __init__(self, session: AsyncSession, *, request_id: str | None = None) -> None:
        self.session = session
        self.request_id = request_id

    async def list_leads(self, *, status: str | None, limit: int, offset: int) -> LeadList:
        filters = []
        if status is not None:
            filters.append(Lead.status == status)

        total_query = select(func.count()).select_from(Lead).where(*filters)
        total = int((await self.session.execute(total_query)).scalar_one())

        query = (
            select(Lead)
            .where(*filters)
            .order_by(Lead.created_at, Lead.synthetic_profile_key)
            .offset(offset)
            .limit(limit)
        )
        items = list((await self.session.execute(query)).scalars().all())
        return LeadList(items=items, total=total)

    async def get_lead(self, lead_id: UUID) -> Lead:
        lead = await self.session.get(Lead, lead_id)
        if lead is None:
            raise LeadNotFoundError(str(lead_id))
        return lead

    async def get_qualification(self, lead_id: UUID) -> QualificationData:
        await self.get_lead(lead_id)
        profile = (
            await self.session.execute(
                select(QualificationProfile).where(QualificationProfile.lead_id == lead_id)
            )
        ).scalar_one_or_none()
        if profile is None:
            raise LeadNotFoundError(f"qualification profile for {lead_id}")

        answers = list(
            (
                await self.session.execute(
                    select(QualificationAnswer)
                    .where(QualificationAnswer.qualification_profile_id == profile.id)
                    .order_by(QualificationAnswer.field_key)
                )
            )
            .scalars()
            .all()
        )
        return QualificationData(profile=profile, answers=answers)

    async def create_lead(self, data: LeadCreate, *, lead_id: UUID | None = None) -> Lead:
        """Stage lead, profile, answers, and event; reject duplicate keys atomically."""

        values = data.model_dump(exclude={"initial_answers"})
        if lead_id is not None:
            values["id"] = lead_id
        statement = (
            insert(Lead)
            .values(**values)
            .on_conflict_do_nothing(index_elements=[Lead.synthetic_profile_key])
            .returning(Lead)
        )
        lead = (await self.session.execute(statement)).scalar_one_or_none()
        if lead is None:
            raise DuplicateLeadError(data.synthetic_profile_key)

        profile = QualificationProfile(lead_id=lead.id)
        self.session.add(profile)
        await self.session.flush()

        for field_key, value in sorted(data.initial_answers.items()):
            self.session.add(
                QualificationAnswer(
                    qualification_profile_id=profile.id,
                    field_key=field_key,
                    value=value,
                    answer_status="PROVISIONAL",
                    source="SEED" if lead_id is not None else "API",
                )
            )

        self.session.add(
            DomainEvent(
                event_id=uuid5(NAMESPACE_URL, f"voice-ai-platform:lead-created:{lead.id}"),
                event_type="lead.created",
                request_id=self.request_id,
                producer="lead-service",
                aggregate_type="lead",
                aggregate_id=lead.id,
                aggregate_version=lead.version,
                idempotency_key=f"lead.created:{lead.id}",
                payload={
                    "lead_id": str(lead.id),
                    "synthetic_profile_key": lead.synthetic_profile_key,
                },
                occurred_at=datetime.now(UTC),
            )
        )
        await self.session.flush()
        return lead

    async def update_lead(self, lead_id: UUID, data: LeadUpdate) -> Lead:
        """Compare-and-swap at the database, then stage the matching outbox event."""

        changes = data.model_dump(exclude_unset=True, exclude={"expected_version"})
        statement = (
            update(Lead)
            .where(Lead.id == lead_id, Lead.version == data.expected_version)
            .values(**changes, version=Lead.version + 1)
            .returning(Lead)
            .execution_options(populate_existing=True)
        )
        lead = (await self.session.execute(statement)).scalar_one_or_none()
        if lead is None:
            await self.get_lead(lead_id)
            raise LeadVersionConflictError(str(lead_id))

        self.session.add(
            DomainEvent(
                event_id=uuid5(
                    NAMESPACE_URL,
                    f"voice-ai-platform:lead-updated:{lead.id}:{lead.version}",
                ),
                event_type="lead.updated",
                request_id=self.request_id,
                producer="lead-service",
                aggregate_type="lead",
                aggregate_id=lead.id,
                aggregate_version=lead.version,
                idempotency_key=f"lead.updated:{lead.id}:{lead.version}",
                payload={"lead_id": str(lead.id), "changed_fields": sorted(changes)},
                occurred_at=datetime.now(UTC),
            )
        )
        await self.session.flush()
        return lead

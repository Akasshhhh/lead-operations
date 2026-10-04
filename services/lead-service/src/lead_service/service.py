"""Lead domain operations and persistence orchestration."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from voice_platform_db.models import (
    DomainEvent,
    Lead,
    LeadScore,
    LeadScoreHistory,
    QualificationAnswer,
    QualificationProfile,
)

from .schemas import LeadCreate, LeadUpdate, QualificationUpdate
from .scoring import FIELD_WEIGHTS, RULE_VERSION, evaluate_answers, valid_field_value


class LeadNotFoundError(LookupError):
    """Raised when a requested lead does not exist."""


class DuplicateLeadError(ValueError):
    """Raised when a synthetic profile key is already in use."""


class LeadVersionConflictError(ValueError):
    """Raised when an optimistic update uses a stale lead version."""


class QualificationVersionConflictError(ValueError):
    """Raised when a live qualification update uses a stale profile version."""


class QualificationIdempotencyConflictError(ValueError):
    """Raised when a turn ID is reused with different facts."""


class QualificationValidationError(ValueError):
    """Raised when a supported qualification fact is not valid for its field."""


@dataclass(frozen=True, slots=True)
class LeadList:
    items: list[Lead]
    total: int


@dataclass(frozen=True, slots=True)
class QualificationData:
    profile: QualificationProfile
    answers: list[QualificationAnswer]
    score: LeadScore | None = None


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
        score = await self.session.get(LeadScore, lead_id)
        return QualificationData(profile=profile, answers=answers, score=score)

    async def apply_qualification_update(
        self, lead_id: UUID, data: QualificationUpdate
    ) -> QualificationData:
        """Apply validated live facts and recalculate the authoritative score atomically."""
        serialized_facts = [fact.model_dump(mode="json") for fact in data.facts]
        request_hash = hashlib.sha256(
            json.dumps(
                {
                    "lead_id": str(lead_id),
                    "conversation_id": str(data.conversation_id),
                    "facts": sorted(serialized_facts, key=lambda fact: fact["field_key"]),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        # Recognize already-persisted receipts without rewriting historical events.
        legacy_hash = hashlib.sha256(
            json.dumps(serialized_facts, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        idempotency_key = f"qualification.updated:{data.turn_id}"
        profile = (
            await self.session.execute(
                select(QualificationProfile)
                .where(QualificationProfile.lead_id == lead_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if profile is None:
            await self.get_lead(lead_id)
            raise LeadNotFoundError(f"qualification profile for {lead_id}")
        # Check after acquiring the profile lock: a competing request may have just committed.
        existing_event = await self.session.scalar(
            select(DomainEvent).where(DomainEvent.idempotency_key == idempotency_key)
        )
        if existing_event is not None:
            payload = existing_event.payload
            if (
                not isinstance(payload, dict)
                or payload.get("request_hash") not in {request_hash, legacy_hash}
                or payload.get("lead_id") != str(lead_id)
                or payload.get("conversation_id") != str(data.conversation_id)
            ):
                raise QualificationIdempotencyConflictError(str(data.turn_id))
            return await self.get_qualification(lead_id)

        if profile.version != data.expected_profile_version:
            raise QualificationVersionConflictError(str(lead_id))

        answers = list(
            (
                await self.session.execute(
                    select(QualificationAnswer)
                    .where(QualificationAnswer.qualification_profile_id == profile.id)
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )
        by_key = {answer.field_key: answer for answer in answers}
        for fact in data.facts:
            if fact.field_key in FIELD_WEIGHTS and fact.status == "CONFIRMED":
                if not valid_field_value(fact.field_key, fact.value):
                    raise QualificationValidationError(fact.field_key)
            answer = by_key.get(fact.field_key)
            if answer is None:
                answer = QualificationAnswer(
                    qualification_profile_id=profile.id,
                    field_key=fact.field_key,
                    value=fact.value,
                )
                self.session.add(answer)
                by_key[fact.field_key] = answer

            same_value = answer.value == fact.value
            if fact.status == "CONTRADICTORY":
                answer.answer_status = "CONTRADICTORY"
                answer.conflict_value = fact.value
            elif (
                answer.answer_status == "CONFIRMED" and not same_value and not fact.resolve_conflict
            ):
                answer.answer_status = "CONTRADICTORY"
                answer.conflict_value = fact.value
            elif answer.answer_status == "CONTRADICTORY" and not fact.resolve_conflict:
                answer.conflict_value = fact.value
            else:
                answer.value = fact.value
                answer.conflict_value = None
                answer.answer_status = fact.status
            answer.confidence = (
                Decimal(str(fact.confidence)) if fact.confidence is not None else None
            )
            answer.source = "CONVERSATION"
            answer.conversation_id = data.conversation_id

        await self.session.flush()
        result = evaluate_answers(list(by_key.values()))
        profile.status = result.profile_status
        profile.completeness = result.completeness
        profile.version += 1

        score = await self.session.get(LeadScore, lead_id, with_for_update=True)
        previous_score = score.score if score is not None else None
        if score is None:
            score = LeadScore(lead_id=lead_id)
            self.session.add(score)
        score.score = result.score
        score.classification = result.classification
        score.reasons = result.reasons
        score.rule_version = RULE_VERSION
        score.calculated_at = datetime.now(UTC)
        self.session.add(
            LeadScoreHistory(
                lead_id=lead_id,
                conversation_id=data.conversation_id,
                previous_score=previous_score,
                new_score=result.score,
                classification=result.classification,
                reasons=result.reasons,
                rule_version=RULE_VERSION,
            )
        )
        self.session.add(
            DomainEvent(
                event_id=uuid5(NAMESPACE_URL, f"voice-ai-platform:qualification:{data.turn_id}"),
                event_type="qualification.updated",
                producer="lead-service",
                aggregate_type="qualification",
                aggregate_id=profile.id,
                aggregate_version=profile.version,
                correlation_id=data.conversation_id,
                causation_id=data.turn_id,
                request_id=self.request_id,
                idempotency_key=idempotency_key,
                payload={
                    "lead_id": str(lead_id),
                    "profile_id": str(profile.id),
                    "conversation_id": str(data.conversation_id),
                    "turn_id": str(data.turn_id),
                    "request_hash": request_hash,
                    "score": result.score,
                    "classification": result.classification,
                    "rule_version": RULE_VERSION,
                    "contradictions": result.contradictions,
                },
                occurred_at=datetime.now(UTC),
            )
        )
        await self.session.flush()
        return QualificationData(profile=profile, answers=list(by_key.values()), score=score)

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

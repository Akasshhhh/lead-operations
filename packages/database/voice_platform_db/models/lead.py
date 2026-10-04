"""Lead-owned persistence models."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, utc_now


class Lead(Base):
    __tablename__ = "leads"
    __table_args__ = {"schema": "lead"}

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    synthetic_profile_key: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    intent: Mapped[str | None] = mapped_column(String(160))
    target_country: Mapped[str | None] = mapped_column(String(80))
    preferred_language: Mapped[str] = mapped_column(String(16), nullable=False, default="en")
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="NEW", server_default="NEW"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
        server_default=text("now()"),
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")


class QualificationProfile(Base):
    __tablename__ = "qualification_profiles"
    __table_args__ = (
        CheckConstraint("completeness BETWEEN 0 AND 100", name="qualification_completeness_range"),
        {"schema": "lead"},
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    lead_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("lead.leads.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="INCOMPLETE", server_default="INCOMPLETE"
    )
    completeness: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
        server_default=text("now()"),
    )


class QualificationAnswer(Base):
    __tablename__ = "qualification_answers"
    __table_args__ = (
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="answer_confidence_range",
        ),
        UniqueConstraint(
            "qualification_profile_id", "field_key", name="uq_qualification_answer_field"
        ),
        {"schema": "lead"},
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    qualification_profile_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("lead.qualification_profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    field_key: Mapped[str] = mapped_column(String(80), nullable=False)
    value: Mapped[object] = mapped_column(JSONB, nullable=False)
    normalized_value: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    answer_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="PROVISIONAL", server_default="PROVISIONAL"
    )
    source: Mapped[str] = mapped_column(
        String(32), nullable=False, default="AGENT", server_default="AGENT"
    )
    conversation_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
        server_default=text("now()"),
    )


class LeadScore(Base):
    __tablename__ = "lead_scores"
    __table_args__ = (
        CheckConstraint("score BETWEEN 0 AND 100", name="lead_score_range"),
        {"schema": "lead"},
    )

    lead_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("lead.leads.id", ondelete="CASCADE"),
        primary_key=True,
    )
    score: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    classification: Mapped[str] = mapped_column(
        String(16), nullable=False, default="COLD", server_default="COLD"
    )
    rule_version: Mapped[str] = mapped_column(String(32), nullable=False)
    calculated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=text("now()")
    )


class LeadScoreHistory(Base):
    __tablename__ = "lead_score_history"
    __table_args__ = (
        CheckConstraint("new_score BETWEEN 0 AND 100", name="score_history_new_range"),
        CheckConstraint(
            "previous_score IS NULL OR previous_score BETWEEN 0 AND 100",
            name="score_history_previous_range",
        ),
        {"schema": "lead"},
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    lead_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("lead.leads.id", ondelete="CASCADE"),
        nullable=False,
    )
    conversation_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    previous_score: Mapped[int | None] = mapped_column(Integer)
    new_score: Mapped[int] = mapped_column(Integer, nullable=False)
    classification: Mapped[str] = mapped_column(String(16), nullable=False)
    reasons: Mapped[object] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    rule_version: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=text("now()")
    )

"""Conversation-owned persistence models."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, utc_now


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        CheckConstraint(
            "state IN ('CREATED', 'CONNECTING', 'GREETING', 'DISCOVERY', 'QUALIFICATION', "
            "'SCORING', 'DECISION', 'FOLLOW_UP', 'HUMAN_HANDOFF', 'COMPLETED', 'FAILED')",
            name="conversation_state_values",
        ),
        {"schema": "conversation"},
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    lead_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default="CREATED", server_default="CREATED"
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=sql_text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
        server_default=sql_text("now()"),
    )


class Call(Base):
    __tablename__ = "calls"
    __table_args__ = (
        CheckConstraint(
            "status IN ('CREATED', 'CONNECTING', 'CONNECTED', 'RECONNECTING', 'ENDED', 'FAILED')",
            name="call_status_values",
        ),
        {"schema": "conversation"},
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("conversation.conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    transport: Mapped[str] = mapped_column(
        String(32), nullable=False, default="WEBRTC", server_default="WEBRTC"
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="CREATED", server_default="CREATED"
    )
    runtime_instance_id: Mapped[str | None] = mapped_column(String(120))
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disconnect_reason: Mapped[str | None] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=sql_text("now()")
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint(
            "speaker IN ('USER', 'AGENT', 'SYSTEM', 'TOOL')", name="message_speaker_values"
        ),
        UniqueConstraint(
            "conversation_id", "sequence_number", name="uq_message_conversation_sequence"
        ),
        {"schema": "conversation"},
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("conversation.conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    call_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("conversation.calls.id", ondelete="SET NULL")
    )
    speaker: Mapped[str] = mapped_column(String(16), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    provider: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(128))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    message_metadata: Mapped[object] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=sql_text("now()")
    )


class TranscriptSegment(Base):
    __tablename__ = "transcript_segments"
    __table_args__ = (
        CheckConstraint(
            "speaker IN ('USER', 'AGENT', 'SYSTEM', 'TOOL')", name="transcript_speaker_values"
        ),
        UniqueConstraint(
            "conversation_id", "sequence_number", name="uq_transcript_conversation_sequence"
        ),
        UniqueConstraint(
            "conversation_id", "provider_segment_id", name="uq_transcript_provider_segment"
        ),
        {"schema": "conversation"},
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("conversation.conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    call_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("conversation.calls.id", ondelete="SET NULL")
    )
    speaker: Mapped[str] = mapped_column(String(16), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    segment_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="FINAL", server_default="FINAL"
    )
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    is_final: Mapped[bool] = mapped_column(nullable=False, default=True, server_default="true")
    provider: Mapped[str | None] = mapped_column(String(64))
    provider_segment_id: Mapped[str | None] = mapped_column(String(160))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    segment_metadata: Mapped[object] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=sql_text("now()")
    )


class ConversationSummary(Base):
    __tablename__ = "conversation_summaries"
    __table_args__ = {"schema": "conversation"}

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("conversation.conversations.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    intent: Mapped[str | None] = mapped_column(String(160))
    concerns: Mapped[object] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    next_action: Mapped[str | None] = mapped_column(String(80))
    qualification_changes: Mapped[object] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    generated_by: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=sql_text("now()")
    )

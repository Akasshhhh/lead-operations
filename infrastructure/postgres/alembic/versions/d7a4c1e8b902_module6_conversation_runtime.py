"""Add Module 6 conversation runtime state and live qualification metadata.

Revision ID: d7a4c1e8b902
Revises: 913be7264a01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d7a4c1e8b902"
down_revision: str | None = "913be7264a01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "lead_scores",
        sa.Column("reasons", postgresql.JSONB(), server_default="[]", nullable=False),
        schema="lead",
    )
    op.add_column(
        "qualification_answers", sa.Column("conflict_value", postgresql.JSONB()), schema="lead"
    )

    op.add_column(
        "conversations", sa.Column("failure_reason", sa.String(160)), schema="conversation"
    )
    op.add_column(
        "conversations",
        sa.Column("failure_context", postgresql.JSONB(), server_default="{}", nullable=False),
        schema="conversation",
    )
    op.add_column("conversations", sa.Column("next_action", sa.String(80)), schema="conversation")
    op.add_column(
        "conversations",
        sa.Column("last_turn_at", sa.DateTime(timezone=True)),
        schema="conversation",
    )

    op.add_column(
        "calls",
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        schema="conversation",
    )
    op.add_column(
        "calls",
        sa.Column("reconnect_attempts", sa.Integer(), server_default="0", nullable=False),
        schema="conversation",
    )
    op.add_column("calls", sa.Column("failure_reason", sa.String(160)), schema="conversation")
    op.add_column(
        "calls", sa.Column("failed_at", sa.DateTime(timezone=True)), schema="conversation"
    )

    op.add_column(
        "messages",
        sa.Column("turn_status", sa.String(16), server_default="PENDING", nullable=False),
        schema="conversation",
    )
    op.add_column("messages", sa.Column("qualification_error", sa.Text()), schema="conversation")
    op.add_column(
        "messages", sa.Column("qualification_update_id", sa.UUID()), schema="conversation"
    )
    op.create_check_constraint(
        "message_turn_status_values",
        "messages",
        "turn_status IN ('PENDING', 'APPLIED', 'FAILED')",
        schema="conversation",
    )

    op.create_index(
        "uq_conversation_one_active_per_lead",
        "conversations",
        ["lead_id"],
        unique=True,
        schema="conversation",
        postgresql_where=sa.text("state NOT IN ('COMPLETED', 'FAILED')"),
    )
    op.create_index(
        "uq_call_one_active_per_conversation",
        "calls",
        ["conversation_id"],
        unique=True,
        schema="conversation",
        postgresql_where=sa.text(
            "status IN ('CREATED', 'CONNECTING', 'CONNECTED', 'RECONNECTING')"
        ),
    )
    op.create_index(
        "uq_message_one_pending_per_conversation",
        "messages",
        ["conversation_id"],
        unique=True,
        schema="conversation",
        postgresql_where=sa.text("turn_status = 'PENDING'"),
    )


def downgrade() -> None:
    op.drop_column("lead_scores", "reasons", schema="lead")
    op.drop_index(
        "uq_message_one_pending_per_conversation", table_name="messages", schema="conversation"
    )
    op.drop_index("uq_call_one_active_per_conversation", table_name="calls", schema="conversation")
    op.drop_index(
        "uq_conversation_one_active_per_lead", table_name="conversations", schema="conversation"
    )
    op.drop_constraint(
        "message_turn_status_values", "messages", schema="conversation", type_="check"
    )
    op.drop_column("messages", "qualification_update_id", schema="conversation")
    op.drop_column("messages", "qualification_error", schema="conversation")
    op.drop_column("messages", "turn_status", schema="conversation")
    op.drop_column("calls", "failed_at", schema="conversation")
    op.drop_column("calls", "failure_reason", schema="conversation")
    op.drop_column("calls", "reconnect_attempts", schema="conversation")
    op.drop_column("calls", "version", schema="conversation")
    op.drop_column("conversations", "last_turn_at", schema="conversation")
    op.drop_column("conversations", "next_action", schema="conversation")
    op.drop_column("conversations", "failure_context", schema="conversation")
    op.drop_column("conversations", "failure_reason", schema="conversation")
    op.drop_column("qualification_answers", "conflict_value", schema="lead")

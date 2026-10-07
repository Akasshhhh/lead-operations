"""Call-scoped qualification replacements and existing history audit snapshots.

Revision ID: f1a7c9e2b604
Revises: e8f2a6b3c901
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f1a7c9e2b604"
down_revision: str | None = "e8f2a6b3c901"
branch_labels: str | Sequence[str] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("qualification_answers", sa.Column("call_id", postgresql.UUID()), schema="lead")
    op.add_column(
        "qualification_answers", sa.Column("pending_value", postgresql.JSONB()), schema="lead"
    )
    op.add_column(
        "qualification_answers", sa.Column("pending_call_id", postgresql.UUID()), schema="lead"
    )
    op.add_column(
        "lead_score_history",
        sa.Column("answer_changes", postgresql.JSONB(), nullable=False, server_default="[]"),
        schema="lead",
    )

    # One-time migration uses Conversation's durable admitted call identity, never LLM IDs.
    # Redacted/seeded rows without a matching receipt remain historical, with unknown call.
    op.execute("""
        UPDATE lead.qualification_answers AS answer
        SET call_id = receipt.call_id
        FROM (
            SELECT DISTINCT ON (answer.id) answer.id, message.call_id
            FROM lead.qualification_answers AS answer
            JOIN platform.domain_events AS event
              ON event.aggregate_id = answer.qualification_profile_id
             AND event.producer = 'lead-service'
             AND event.event_type = 'qualification.updated'
            JOIN conversation.messages AS message
              ON message.id = event.causation_id
             AND message.conversation_id = answer.conversation_id
            CROSS JOIN LATERAL jsonb_array_elements(
                COALESCE(message.metadata->'qualification_facts', '[]'::jsonb)
            ) AS fact
            WHERE fact->>'field_key' = answer.field_key AND message.call_id IS NOT NULL
              AND answer.source = 'CONVERSATION'
            ORDER BY answer.id, event.aggregate_version DESC
        ) AS receipt
        WHERE receipt.id = answer.id
    """)


def downgrade() -> None:
    op.drop_column("lead_score_history", "answer_changes", schema="lead")
    for column in ("pending_call_id", "pending_value", "call_id"):
        op.drop_column("qualification_answers", column, schema="lead")

"""Evaluate timestamp defaults at insert time instead of migration time.

Revision ID: 913be7264a01
Revises: 6b30f517c820

Only defaults change; existing timestamps are preserved. Downgrade restores
the previous literal-default behavior, using the downgrade transaction's time.
"""

import sqlalchemy as sa
from alembic import op

revision = "913be7264a01"
down_revision = "6b30f517c820"
branch_labels = None
depends_on = None

TIMESTAMPS = {
    "lead.leads": ("created_at", "updated_at"),
    "lead.qualification_profiles": ("updated_at",),
    "lead.qualification_answers": ("updated_at",),
    "lead.lead_scores": ("calculated_at",),
    "lead.lead_score_history": ("created_at",),
    "conversation.conversations": ("created_at", "updated_at"),
    "conversation.calls": ("created_at",),
    "conversation.messages": ("created_at",),
    "conversation.transcript_segments": ("created_at",),
    "conversation.conversation_summaries": ("created_at",),
    "workflow.followups": ("created_at", "updated_at"),
    "workflow.followup_attempts": ("started_at",),
    "workflow.handoffs": ("requested_at",),
    "evaluation.scenarios": ("created_at",),
    "evaluation.evaluation_runs": ("created_at",),
    "evaluation.evaluation_results": ("created_at",),
    "platform.domain_events": ("occurred_at",),
    "platform.processed_events": ("processed_at",),
    "platform.provider_health": ("updated_at",),
    "platform.fault_injections": ("created_at",),
    "platform.audit_log": ("created_at",),
}


def change_defaults(default: str | sa.TextClause) -> None:
    for qualified_name, columns in TIMESTAMPS.items():
        schema, table = qualified_name.split(".")
        for column in columns:
            op.alter_column(
                table,
                column,
                schema=schema,
                existing_type=sa.DateTime(timezone=True),
                server_default=default,
            )


def upgrade() -> None:
    change_defaults(sa.text("now()"))


def downgrade() -> None:
    change_defaults("now()")

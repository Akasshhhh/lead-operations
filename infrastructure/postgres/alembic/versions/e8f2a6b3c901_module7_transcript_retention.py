"""Add transcript redaction metadata for Module 7 retention.

Revision ID: e8f2a6b3c901
Revises: d7a4c1e8b902
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e8f2a6b3c901"
down_revision: str | None = "d7a4c1e8b902"
branch_labels: str | Sequence[str] | None = None
depends_on: str | None = None


def upgrade() -> None:
    for table in ("messages", "transcript_segments"):
        op.add_column(
            table, sa.Column("redacted_at", sa.DateTime(timezone=True)), schema="conversation"
        )
        op.add_column(table, sa.Column("redaction_reason", sa.String(80)), schema="conversation")
        op.add_column(table, sa.Column("content_sha256", sa.String(64)), schema="conversation")


def downgrade() -> None:
    for table in ("transcript_segments", "messages"):
        op.drop_column(table, "content_sha256", schema="conversation")
        op.drop_column(table, "redaction_reason", schema="conversation")
        op.drop_column(table, "redacted_at", schema="conversation")

"""Index the unpublished outbox without changing existing event records."""

import sqlalchemy as sa
from alembic import op

revision = "6b30f517c820"
down_revision = "ee2c5d44776b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_domain_events_unpublished",
        "domain_events",
        ["occurred_at", "event_id"],
        schema="platform",
        postgresql_where=sa.text("published_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_domain_events_unpublished", table_name="domain_events", schema="platform")

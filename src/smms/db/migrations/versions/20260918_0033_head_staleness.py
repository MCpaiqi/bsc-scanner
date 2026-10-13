"""Persist BSC provider-head advancement for live-edge correctness.

Revision ID: 20260918_0033
Revises: 20260918_0032
"""

import sqlalchemy as sa
from alembic import op

revision = "20260918_0033"
down_revision = "20260918_0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "runtime_worker_status",
        sa.Column("head_last_advanced_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "runtime_worker_status",
        sa.Column("head_staleness_seconds", sa.Float()),
    )


def downgrade() -> None:
    op.drop_column("runtime_worker_status", "head_staleness_seconds")
    op.drop_column("runtime_worker_status", "head_last_advanced_at")

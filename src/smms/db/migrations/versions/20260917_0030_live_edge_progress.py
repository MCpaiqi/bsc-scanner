"""Persist authoritative BSC live-edge progress.

Revision ID: 20260917_0030
Revises: 20260915_0029
"""

import sqlalchemy as sa
from alembic import op

revision = "20260917_0030"
down_revision = "20260915_0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runtime_worker_status", sa.Column("head_block", sa.BigInteger()))
    op.add_column("runtime_worker_status", sa.Column("block_lag", sa.BigInteger()))
    op.add_column("runtime_worker_status", sa.Column("last_processed_at", sa.DateTime(timezone=True)))
    op.add_column("runtime_worker_status", sa.Column("ingestion_phase", sa.String(length=16)))
    op.add_column(
        "runtime_worker_status",
        sa.Column("live_ready", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "runtime_worker_status",
        sa.Column("last_chunk_metrics", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )


def downgrade() -> None:
    for name in (
        "last_chunk_metrics", "live_ready", "ingestion_phase", "last_processed_at",
        "block_lag", "head_block",
    ):
        op.drop_column("runtime_worker_status", name)

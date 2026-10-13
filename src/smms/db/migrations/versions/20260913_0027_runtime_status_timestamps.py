"""Expand shared worker status timestamps.

Revision ID: 20260913_0027
Revises: 20260913_0026
"""

import sqlalchemy as sa
from alembic import op

revision = "20260913_0027"
down_revision = "20260913_0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name in (
        "last_raw_log_at", "last_trade_at", "last_market_snapshot_at",
        "last_radar_update_at", "last_event_published_at", "last_event_forwarded_at",
    ):
        op.add_column("runtime_worker_status", sa.Column(name, sa.DateTime(timezone=True)))


def downgrade() -> None:
    for name in (
        "last_event_forwarded_at", "last_event_published_at", "last_radar_update_at",
        "last_market_snapshot_at", "last_trade_at", "last_raw_log_at",
    ):
        op.drop_column("runtime_worker_status", name)

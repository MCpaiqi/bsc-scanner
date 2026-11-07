"""Transactional Radar event envelopes and fenced delivery state."""

import sqlalchemy as sa
from alembic import context, op

revision = "20261107_0044"
down_revision = "20261106_0043"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("radar_event_outbox",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_id", sa.String(64), nullable=False, unique=True),
        sa.Column("effect_key", sa.String(256), nullable=False, unique=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("chain", sa.String(32), nullable=False),
        sa.Column("token_address", sa.String(128), nullable=False),
        sa.Column("data_json", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("lease_token", sa.String(32)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column("last_error_code", sa.String(64)),
        sa.CheckConstraint("status IN ('PENDING','DELIVERED')", name="ck_radar_outbox_status"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_radar_outbox_attempts"),
    )
    op.create_index("ix_radar_outbox_due", "radar_event_outbox", ["status", "available_at", "id"])


def downgrade():
    if context.is_offline_mode():
        raise RuntimeError("outbox downgrade requires an online delivery-history check")
    if op.get_bind().scalar(sa.text("SELECT EXISTS(SELECT 1 FROM radar_event_outbox)")):
        raise RuntimeError("cannot discard committed event/delivery history")
    op.drop_table("radar_event_outbox")

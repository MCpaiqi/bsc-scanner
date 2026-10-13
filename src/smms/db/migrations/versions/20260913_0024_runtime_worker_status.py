"""Add shared runtime worker status projection.

Revision ID: 20260913_0024
Revises: 20260913_0023
"""
import sqlalchemy as sa
from alembic import op

revision = "20260913_0024"
down_revision = "20260913_0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "runtime_worker_status",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("worker_name", sa.String(64), nullable=False, unique=True),
        sa.Column("instance_id", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("transport", sa.String(32)),
        sa.Column("provider", sa.String(64)),
        sa.Column("last_block", sa.Integer()),
        sa.Column("checkpoint_block", sa.Integer()),
        sa.Column("last_event_at", sa.DateTime(timezone=True)),
        sa.Column("last_success_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.String(500)),
        sa.Column("lag", sa.Integer()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_runtime_worker_status_updated", "runtime_worker_status", ["worker_name", "updated_at"])


def downgrade() -> None:
    op.drop_index("ix_runtime_worker_status_updated", table_name="runtime_worker_status")
    op.drop_table("runtime_worker_status")

"""Add the PostgreSQL-backed priority radar audit queue."""

import sqlalchemy as sa
from alembic import op

revision = "20260912_0016"
down_revision = "20260912_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "radar_audit_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("candidate_id", sa.Integer(), sa.ForeignKey("radar_candidates.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chain", sa.String(32), nullable=False),
        sa.Column("token_address", sa.String(128), nullable=False),
        sa.Column("reason", sa.String(256), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("worker_id", sa.String(128)),
        sa.Column("last_error_code", sa.String(32)),
        sa.Column("last_error_message", sa.String(1000)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_radar_audit_claim", "radar_audit_jobs", ["status", "next_run_at", "priority"])
    op.create_index("ix_radar_audit_token", "radar_audit_jobs", ["chain", "token_address", "status"])
    op.create_index("ix_radar_audit_jobs_candidate_id", "radar_audit_jobs", ["candidate_id"])


def downgrade() -> None:
    op.drop_index("ix_radar_audit_jobs_candidate_id", table_name="radar_audit_jobs")
    op.drop_index("ix_radar_audit_token", table_name="radar_audit_jobs")
    op.drop_index("ix_radar_audit_claim", table_name="radar_audit_jobs")
    op.drop_table("radar_audit_jobs")


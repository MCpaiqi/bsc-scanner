"""Persist Alpha work, confirmed query receipts and versioned pending state."""

import sqlalchemy as sa
from alembic import context, op

revision = "20261106_0036"
down_revision = "20261105_0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("alpha_block_anchors",
                    sa.Column("chain_id", sa.Integer(), primary_key=True),
                    sa.Column("block_number", sa.BigInteger(), primary_key=True),
                    sa.Column("block_hash", sa.String(128), nullable=False),
                    sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("alpha_work_cursors",
                    sa.Column("version", sa.String(64), primary_key=True),
                    sa.Column("last_pair_id", sa.Integer(), nullable=False))
    op.create_table(
        "alpha_coverage_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("pair_id", sa.Integer(), sa.ForeignKey("pairs.id"), nullable=False, unique=True),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("next_block", sa.Integer()),
        sa.Column("target_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("lease_token", sa.String(64)),
        sa.Column("leased_until", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.String(64)),
    )
    op.create_index("ix_alpha_coverage_due", "alpha_coverage_jobs", ["status", "next_attempt_at", "id"])
    op.create_table(
        "alpha_coverage_receipts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("alpha_coverage_jobs.id"), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("canonical", sa.Boolean(), nullable=False),
        sa.Column("start_block", sa.Integer(), nullable=False),
        sa.Column("end_block", sa.Integer(), nullable=False),
        sa.Column("start_hash", sa.String(128), nullable=False),
        sa.Column("end_hash", sa.String(128), nullable=False),
        sa.Column("start_parent_hash", sa.String(128), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_count", sa.Integer(), nullable=False),
        sa.Column("decoded_count", sa.Integer(), nullable=False),
        sa.Column("query_json", sa.JSON(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("job_id", "generation", "start_block", "end_block", name="uq_alpha_coverage_range"),
    )
    op.create_index("ix_alpha_coverage_proof", "alpha_coverage_receipts",
                    ["job_id", "generation", "canonical", "start_block"])
    with op.batch_alter_table("alpha_snapshots") as batch:
        batch.alter_column("alpha_score", existing_type=sa.Numeric(6, 2), nullable=True)
        batch.add_column(sa.Column("task_status", sa.String(32), nullable=False, server_default="LEGACY"))
        batch.add_column(sa.Column("next_evaluation_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("coverage_generation", sa.Integer()))
        for name in ("entry_vwap", "current_return", "max_tick_return", "max_realisable_return"):
            batch.alter_column(name, existing_type=sa.Numeric(38, 18), type_=sa.Numeric())
    op.create_index("ix_alpha_snapshot_due", "alpha_snapshots",
                    ["score_version", "task_status", "next_evaluation_at", "id"])


def downgrade() -> None:
    if not context.is_offline_mode():
        bind = op.get_bind()
        if (bind.execute(sa.text("SELECT 1 FROM alpha_coverage_jobs LIMIT 1")).first()
                or bind.execute(sa.text("SELECT 1 FROM alpha_block_anchors LIMIT 1")).first()
                or bind.execute(sa.text("SELECT 1 FROM alpha_snapshots WHERE task_status != 'LEGACY' LIMIT 1")).first()):
            raise RuntimeError("Alpha coverage work exists; refusing downgrade without explicit archival")
    op.drop_index("ix_alpha_snapshot_due", table_name="alpha_snapshots")
    with op.batch_alter_table("alpha_snapshots") as batch:
        for name in ("task_status", "next_evaluation_at", "coverage_generation"):
            batch.drop_column(name)
        batch.alter_column("alpha_score", existing_type=sa.Numeric(6, 2), nullable=False)
        for name in ("entry_vwap", "current_return", "max_tick_return", "max_realisable_return"):
            batch.alter_column(name, existing_type=sa.Numeric(), type_=sa.Numeric(38, 18))
    op.drop_table("alpha_coverage_receipts")
    op.drop_table("alpha_coverage_jobs")
    op.drop_table("alpha_work_cursors")
    op.drop_table("alpha_block_anchors")

"""Retain invalidated Alpha inputs and enqueue append-only reconstructions."""

import sqlalchemy as sa
from alembic import context, op

revision = "20261106_0037"
down_revision = "20261106_0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("alpha_snapshots") as batch:
        batch.add_column(sa.Column("input_revision", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("supersedes_id", sa.Integer()))
        batch.add_column(sa.Column("input_manifest", sa.JSON(), nullable=False, server_default="{}"))
        batch.drop_constraint("uq_alpha_snapshot_window", type_="unique")
        batch.create_unique_constraint("uq_alpha_snapshot_window_revision", [
            "chain", "token_address", "pair_address", "observation_seconds", "score_version", "input_revision",
        ])
        batch.create_foreign_key("fk_alpha_snapshot_predecessor", "alpha_snapshots", ["supersedes_id"], ["id"])
    # Existing scores cannot retroactively acquire proof of their exact inputs.
    # Keep their values; bounded repair appends a fresh revision. The old removed
    # log path did not invalidate coverage, so old receipts require fresh scans.
    op.execute(sa.text("UPDATE alpha_snapshots SET task_status = 'INVALIDATED', next_evaluation_at = NULL "
                       "WHERE score_version = 'experimental-v3' AND alpha_score IS NOT NULL"))
    op.execute(sa.text("UPDATE alpha_coverage_receipts SET canonical = false"))
    op.execute(sa.text(
        "UPDATE alpha_coverage_jobs SET generation = generation + 1, "
        "next_block = (SELECT creation_block FROM pairs WHERE pairs.id = alpha_coverage_jobs.pair_id), "
        "next_attempt_at = CURRENT_TIMESTAMP, lease_token = NULL, leased_until = NULL, "
        "status = CASE WHEN EXISTS (SELECT 1 FROM pairs WHERE pairs.id = alpha_coverage_jobs.pair_id "
        "AND pairs.canonical = true AND pairs.removed = false) THEN 'PENDING' ELSE 'INVALIDATED' END"
    ))


def downgrade() -> None:
    if not context.is_offline_mode() and op.get_bind().execute(sa.text(
        "SELECT 1 FROM alpha_snapshots WHERE input_revision != 0 OR supersedes_id IS NOT NULL LIMIT 1",
    )).first():
        raise RuntimeError("Alpha research revisions exist; refusing downgrade without explicit archival")
    with op.batch_alter_table("alpha_snapshots") as batch:
        batch.drop_constraint("fk_alpha_snapshot_predecessor", type_="foreignkey")
        batch.drop_constraint("uq_alpha_snapshot_window_revision", type_="unique")
        batch.create_unique_constraint("uq_alpha_snapshot_window", [
            "chain", "token_address", "pair_address", "observation_seconds", "score_version",
        ])
        for name in ("input_manifest", "supersedes_id", "input_revision"):
            batch.drop_column(name)

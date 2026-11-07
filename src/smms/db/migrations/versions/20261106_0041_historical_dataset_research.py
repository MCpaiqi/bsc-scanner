"""Keep legacy dataset payloads, quarantine recipes, append traced revisions."""

import sqlalchemy as sa
from alembic import context, op

revision = "20261106_0041"
down_revision = "20261106_0040"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("backtest_datasets") as batch:
        batch.add_column(sa.Column("source_epoch", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("research_revision", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("snapshot_status", sa.String(24), nullable=False, server_default="INVALIDATED"))
        batch.add_column(sa.Column("previous_snapshot_id", sa.Integer()))
        batch.add_column(sa.Column("input_manifest", sa.JSON(), nullable=False, server_default="{}"))
        batch.drop_constraint("uq_backtest_datasets_version", type_="unique")
        batch.create_unique_constraint("uq_backtest_datasets_version", ["chain", "dataset_version", "research_revision"])
    op.create_index("ix_backtest_datasets_rebuild", "backtest_datasets", ["chain", "source_epoch", "snapshot_status", "id"])


def downgrade():
    if not context.is_offline_mode():
        bind = op.get_bind()
        if bind.execute(sa.text("SELECT 1 FROM backtest_datasets WHERE research_revision > 0 OR source_epoch > 0 "
                                "OR CAST(input_manifest AS VARCHAR) != '{}' LIMIT 1")).first():
            raise RuntimeError("Dataset research revisions exist; refusing lossy downgrade")
        if bind.execute(sa.text("SELECT 1 FROM research_rebuild_jobs WHERE kind='backtest_datasets' LIMIT 1")).first():
            raise RuntimeError("Dataset reconstruction work exists; refusing lossy downgrade")
    op.drop_index("ix_backtest_datasets_rebuild", table_name="backtest_datasets")
    with op.batch_alter_table("backtest_datasets") as batch:
        batch.drop_constraint("uq_backtest_datasets_version", type_="unique")
        batch.create_unique_constraint("uq_backtest_datasets_version", ["dataset_version"])
        for name in ("input_manifest", "previous_snapshot_id", "snapshot_status", "research_revision", "source_epoch"):
            batch.drop_column(name)

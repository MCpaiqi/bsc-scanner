"""Persist reproducible point-in-time backtest datasets."""
import sqlalchemy as sa
from alembic import op

revision = "20260912_0014"
down_revision = "20260912_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "backtest_datasets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("dataset_version", sa.String(64), nullable=False),
        sa.Column("build_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_to", sa.DateTime(timezone=True), nullable=False),
        sa.Column("chain", sa.String(32), nullable=False),
        sa.Column("config_hash", sa.String(64), nullable=False),
        sa.Column("source_coverage_json", sa.JSON(), nullable=False),
        sa.Column("report_json", sa.JSON(), nullable=False),
        sa.UniqueConstraint("dataset_version", name="uq_backtest_datasets_version"),
    )
    op.create_index("ix_backtest_datasets_period", "backtest_datasets", ["chain", "period_from", "period_to"])
    op.create_table(
        "backtest_dataset_rows",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("dataset_id", sa.Integer(), sa.ForeignKey("backtest_datasets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token", sa.String(128), nullable=False),
        sa.Column("signal_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("discovery_timestamp", sa.DateTime(timezone=True)),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.UniqueConstraint("dataset_id", "token", "signal_timestamp", name="uq_backtest_dataset_rows_identity"),
    )
    op.create_index("ix_backtest_dataset_rows_signal_time", "backtest_dataset_rows", ["signal_timestamp"])
    op.create_index("ix_backtest_dataset_rows_dataset_token", "backtest_dataset_rows", ["dataset_id", "token"])


def downgrade() -> None:
    op.drop_index("ix_backtest_dataset_rows_dataset_token", table_name="backtest_dataset_rows")
    op.drop_index("ix_backtest_dataset_rows_signal_time", table_name="backtest_dataset_rows")
    op.drop_table("backtest_dataset_rows")
    op.drop_index("ix_backtest_datasets_period", table_name="backtest_datasets")
    op.drop_table("backtest_datasets")

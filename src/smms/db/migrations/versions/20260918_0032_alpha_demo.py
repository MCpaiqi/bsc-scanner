"""Persist experimental live Alpha snapshots and shadow outcomes."""

import sqlalchemy as sa
from alembic import op

revision = "20260918_0032"
down_revision = "20260917_0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "alpha_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chain", sa.String(length=32), nullable=False),
        sa.Column("token_address", sa.String(length=128), nullable=False),
        sa.Column("pair_address", sa.String(length=128), nullable=False),
        sa.Column("pair_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("age_seconds", sa.BigInteger(), nullable=False),
        sa.Column("observation_seconds", sa.Integer(), nullable=False),
        sa.Column("alpha_score", sa.Numeric(6, 2), nullable=False),
        sa.Column("score_version", sa.String(length=64), nullable=False),
        sa.Column("scored_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("features_json", sa.JSON(), nullable=False),
        sa.Column("components_json", sa.JSON(), nullable=False),
        sa.Column("positive_reasons", sa.JSON(), nullable=False),
        sa.Column("negative_reasons", sa.JSON(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("entry_vwap", sa.Numeric(38, 18)),
        sa.Column("current_return", sa.Numeric(38, 18)),
        sa.Column("max_tick_return", sa.Numeric(38, 18)),
        sa.Column("max_realisable_return", sa.Numeric(38, 18)),
        sa.Column("outcome_json", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("chain", "token_address", "pair_address", "observation_seconds", name="uq_alpha_snapshot_window"),
    )
    op.create_index("ix_alpha_snapshot_live", "alpha_snapshots", ["chain", "snapshot_at", "alpha_score"])
    op.create_index("ix_alpha_snapshot_token", "alpha_snapshots", ["chain", "token_address", "snapshot_at"])


def downgrade() -> None:
    op.drop_index("ix_alpha_snapshot_token", table_name="alpha_snapshots")
    op.drop_index("ix_alpha_snapshot_live", table_name="alpha_snapshots")
    op.drop_table("alpha_snapshots")

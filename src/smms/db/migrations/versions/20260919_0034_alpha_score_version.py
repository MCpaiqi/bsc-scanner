"""Keep v1 and v2 snapshots side by side without rewriting history."""

from alembic import op

revision = "20260919_0034"
down_revision = "20260918_0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("alpha_snapshots") as batch:
        batch.drop_constraint("uq_alpha_snapshot_window", type_="unique")
        batch.create_unique_constraint("uq_alpha_snapshot_window", [
            "chain", "token_address", "pair_address", "observation_seconds", "score_version",
        ])
    op.create_index("ix_alpha_snapshot_tracking", "alpha_snapshots", ["score_version", "snapshot_at"])


def downgrade() -> None:
    # PostgreSQL refuses this constraint if versions coexist; never delete rows
    # to force a destructive downgrade through.
    op.drop_index("ix_alpha_snapshot_tracking", table_name="alpha_snapshots")
    with op.batch_alter_table("alpha_snapshots") as batch:
        batch.drop_constraint("uq_alpha_snapshot_window", type_="unique")
        batch.create_unique_constraint("uq_alpha_snapshot_window", [
            "chain", "token_address", "pair_address", "observation_seconds",
        ])

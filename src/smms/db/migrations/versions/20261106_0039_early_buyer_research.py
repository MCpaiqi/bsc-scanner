"""Preserve and quarantine legacy buyer/position caches; append research revisions."""

import sqlalchemy as sa
from alembic import context, op

revision = "20261106_0039"
down_revision = "20261106_0038"
branch_labels = None
depends_on = None

TABLES = {"early_buyers": "uq_early_buyers_wallet_token",
          "wallet_token_positions": "uq_wallet_token_positions_pair"}


def upgrade():
    for table, constraint in TABLES.items():
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("as_of", sa.DateTime(timezone=True)))
            batch.add_column(sa.Column("recipe_hash", sa.String(64), nullable=False, server_default="legacy"))
            batch.add_column(sa.Column("source_epoch", sa.Integer(), nullable=False, server_default="0"))
            batch.add_column(sa.Column("research_revision", sa.Integer(), nullable=False, server_default="0"))
            batch.add_column(sa.Column("snapshot_status", sa.String(24), nullable=False, server_default="VALID"))
            batch.add_column(sa.Column("previous_snapshot_id", sa.Integer()))
            batch.add_column(sa.Column("input_manifest", sa.JSON(), nullable=False, server_default="{}"))
            batch.drop_constraint(constraint, type_="unique")
        # Old caches have no build time. Reconstruct at the explicit evidence
        # timestamp; missing timestamps cannot establish a historical position.
        timestamp = "first_buy_timestamp" if table == "early_buyers" else "last_trade_timestamp"
        op.execute(sa.text(f"UPDATE {table} SET as_of=COALESCE({timestamp}, '1970-01-01 00:00:00+00')"))
        with op.batch_alter_table(table) as batch:
            batch.alter_column("as_of", nullable=False)
            batch.create_unique_constraint(constraint, ["chain", "wallet_address", "token_address", "as_of",
                                                        "recipe_hash", "research_revision"])
        op.create_index(f"ix_{table}_rebuild", table, ["chain", "source_epoch", "snapshot_status", "id"])
    with op.batch_alter_table("wallet_token_positions") as batch:
        for column in ("cumulative_bought", "cumulative_sold", "current_balance"):
            batch.alter_column(column, existing_type=sa.Numeric(38, 18), nullable=True)


def downgrade():
    if not context.is_offline_mode():
        bind = op.get_bind()
        if bind.execute(sa.text("SELECT 1 FROM research_rebuild_jobs WHERE kind IN "
                                "('early_buyers','wallet_token_positions') LIMIT 1")).first():
            raise RuntimeError("Early buyer reconstruction work exists; refusing lossy downgrade")
        for table in TABLES:
            if bind.execute(sa.text(f"SELECT 1 FROM {table} WHERE research_revision > 0 OR source_epoch > 0 "
                                    "OR CAST(input_manifest AS VARCHAR) != '{}' LIMIT 1")).first():
                raise RuntimeError("Early buyer research revisions exist; refusing lossy downgrade")
        if bind.execute(sa.text("SELECT 1 FROM wallet_token_positions WHERE cumulative_bought IS NULL "
                                "OR cumulative_sold IS NULL OR current_balance IS NULL LIMIT 1")).first():
            raise RuntimeError("Unknown position quantities exist; refusing lossy downgrade")
    for table, constraint in TABLES.items():
        op.drop_index(f"ix_{table}_rebuild", table_name=table)
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(constraint, type_="unique")
            batch.create_unique_constraint(constraint, ["chain", "wallet_address", "token_address"])
            for name in ("input_manifest", "previous_snapshot_id", "snapshot_status", "research_revision",
                         "source_epoch", "recipe_hash", "as_of"):
                batch.drop_column(name)
    with op.batch_alter_table("wallet_token_positions") as batch:
        for column in ("cumulative_bought", "cumulative_sold", "current_balance"):
            batch.alter_column(column, existing_type=sa.Numeric(38, 18), nullable=False)

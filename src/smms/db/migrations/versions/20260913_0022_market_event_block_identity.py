"""Include block hash in canonical market-event identity for reorg replacement logs.

Revision ID: 20260913_0022
Revises: 20260913_0021
"""

import sqlalchemy as sa
from alembic import op

revision = "20260913_0022"
down_revision = "20260913_0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.add_column("market_events", sa.Column("block_hash", sa.String(length=128), nullable=True))
        op.drop_constraint("uq_market_events_identity", "market_events", type_="unique")
        op.create_unique_constraint(
            "uq_market_events_identity",
            "market_events",
            ["chain_id", "tx_hash", "event_index", "block_hash"],
        )
    else:
        with op.batch_alter_table("market_events", recreate="always") as batch:
            batch.add_column(sa.Column("block_hash", sa.String(length=128), nullable=True))
            batch.drop_constraint("uq_market_events_identity", type_="unique")
            batch.create_unique_constraint(
                "uq_market_events_identity",
                ["chain_id", "tx_hash", "event_index", "block_hash"],
            )
    op.create_index(
        "ux_market_events_identity_coalesced",
        "market_events",
        [
            "chain_id",
            "tx_hash",
            "event_index",
            sa.text("coalesce(block_hash, '')"),
        ],
        unique=True,
    )


def downgrade() -> None:
    bind = op.get_bind()
    # SQLite cannot reflect expression indexes after batch table recreation;
    # its downgrade path may therefore have no physical index to remove.
    if bind.dialect.name == "sqlite":
        indexes = sa.inspect(bind).get_indexes("market_events")
        if any(item.get("name") == "ux_market_events_identity_coalesced" for item in indexes):
            op.drop_index("ux_market_events_identity_coalesced", table_name="market_events")
    else:
        op.drop_index("ux_market_events_identity_coalesced", table_name="market_events")
    if bind.dialect.name == "postgresql":
        op.drop_constraint("uq_market_events_identity", "market_events", type_="unique")
        op.create_unique_constraint(
            "uq_market_events_identity",
            "market_events",
            ["chain_id", "tx_hash", "event_index"],
        )
        op.drop_column("market_events", "block_hash")
    else:
        with op.batch_alter_table("market_events", recreate="always") as batch:
            batch.drop_constraint("uq_market_events_identity", type_="unique")
            batch.create_unique_constraint(
                "uq_market_events_identity", ["chain_id", "tx_hash", "event_index"]
            )
            batch.drop_column("block_hash")

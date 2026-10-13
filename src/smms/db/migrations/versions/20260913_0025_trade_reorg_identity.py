"""Make canonical TradeEvent identity reorg-aware.

Revision ID: 20260913_0025
Revises: 20260913_0024
"""
from alembic import op

revision = "20260913_0025"
down_revision = "20260913_0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.drop_constraint("uq_trade_events_chain_tx_index", "trade_events", type_="unique")
        op.create_unique_constraint(
            "uq_trade_events_chain_tx_index",
            "trade_events",
            ["chain", "tx_hash", "event_index", "block_hash"],
        )
    else:
        with op.batch_alter_table("trade_events", recreate="always") as batch:
            batch.drop_constraint("uq_trade_events_chain_tx_index", type_="unique")
            batch.create_unique_constraint(
                "uq_trade_events_chain_tx_index",
                ["chain", "tx_hash", "event_index", "block_hash"],
            )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.drop_constraint("uq_trade_events_chain_tx_index", "trade_events", type_="unique")
        op.create_unique_constraint(
            "uq_trade_events_chain_tx_index",
            "trade_events",
            ["chain", "tx_hash", "event_index"],
        )
    else:
        with op.batch_alter_table("trade_events", recreate="always") as batch:
            batch.drop_constraint("uq_trade_events_chain_tx_index", type_="unique")
            batch.create_unique_constraint(
                "uq_trade_events_chain_tx_index",
                ["chain", "tx_hash", "event_index"],
            )

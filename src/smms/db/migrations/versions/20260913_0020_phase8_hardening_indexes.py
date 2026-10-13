"""Phase 8 bounded wallet intelligence lookup indexes."""

from alembic import op

revision = "20260913_0020"
down_revision = "20260912_0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_trade_events_chain_trader_time",
        "trade_events",
        ["chain", "trader_address", "timestamp"],
    )
    op.create_index(
        "ix_trade_events_chain_token_time",
        "trade_events",
        ["chain", "token_address", "timestamp"],
    )
    op.create_index(
        "ix_wallet_performance_chain_wallet_time",
        "wallet_performance",
        ["chain", "wallet_address", "as_of"],
    )


def downgrade() -> None:
    op.drop_index("ix_wallet_performance_chain_wallet_time", table_name="wallet_performance")
    op.drop_index("ix_trade_events_chain_token_time", table_name="trade_events")
    op.drop_index("ix_trade_events_chain_trader_time", table_name="trade_events")

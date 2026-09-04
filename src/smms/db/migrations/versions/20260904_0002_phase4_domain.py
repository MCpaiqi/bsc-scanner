"""Add chain-neutral Phase 4 domain models.

Revision ID: 20260904_0002
Revises: 20260904_0001
"""

import sqlalchemy as sa
from alembic import op

revision = "20260904_0002"
down_revision = "20260904_0001"
branch_labels = None
depends_on = None

decimal_18 = sa.Numeric(38, 18)
raw_amount = sa.Numeric(50, 0)
score = sa.Numeric(6, 2)


def upgrade() -> None:
    for name, column in (
        ("launchpad", sa.Column("launchpad", sa.String(64))),
        ("creator_address", sa.Column("creator_address", sa.String(128))),
        ("decimals", sa.Column("decimals", sa.Integer())),
        ("total_supply", sa.Column("total_supply", raw_amount)),
        ("first_trade_at", sa.Column("first_trade_at", sa.DateTime(timezone=True))),
        ("graduation_at", sa.Column("graduation_at", sa.DateTime(timezone=True))),
        ("ath_at", sa.Column("ath_at", sa.DateTime(timezone=True))),
        ("ath_price", sa.Column("ath_price", decimal_18)),
        ("graduation_liquidity", sa.Column("graduation_liquidity", decimal_18)),
        ("status", sa.Column("status", sa.String(32), nullable=False, server_default="UNKNOWN")),
        ("outcome_class", sa.Column("outcome_class", sa.String(32))),
    ):
        op.add_column("tokens", column)
    op.create_index("ix_tokens_launchpad", "tokens", ["launchpad"])
    op.create_index("ix_tokens_creator_address", "tokens", ["creator_address"])

    op.create_table(
        "trade_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chain", sa.String(32), nullable=False),
        sa.Column("launchpad", sa.String(64)),
        sa.Column("token_address", sa.String(128), nullable=False),
        sa.Column("trader_address", sa.String(128), nullable=False),
        sa.Column("tx_hash", sa.String(128), nullable=False),
        sa.Column("block_number", sa.BigInteger()),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("token_amount", decimal_18),
        sa.Column("native_amount", decimal_18),
        sa.Column("price", decimal_18),
        sa.Column("market_cap", decimal_18),
        sa.Column("liquidity", decimal_18),
        sa.Column("event_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source", sa.String(128), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.UniqueConstraint("chain", "tx_hash", "event_index", name="uq_trade_events_chain_tx_index"),
    )
    op.create_index("ix_trade_events_chain", "trade_events", ["chain"])
    op.create_index("ix_trade_events_launchpad", "trade_events", ["launchpad"])
    op.create_index("ix_trade_events_token_address", "trade_events", ["token_address"])
    op.create_index("ix_trade_events_trader_address", "trade_events", ["trader_address"])
    op.create_index("ix_trade_events_timestamp", "trade_events", ["timestamp"])
    op.create_index("ix_trade_events_token_time", "trade_events", ["token_address", "timestamp"])
    op.create_index("ix_trade_events_wallet_time", "trade_events", ["trader_address", "timestamp"])
    op.create_index("ix_trade_events_launchpad_time", "trade_events", ["launchpad", "timestamp"])

    op.create_table(
        "token_lifecycle_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chain", sa.String(32), nullable=False),
        sa.Column("launchpad", sa.String(64)),
        sa.Column("token_address", sa.String(128), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tx_hash", sa.String(128)),
        sa.Column("block_number", sa.BigInteger()),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.UniqueConstraint(
            "chain", "token_address", "event_type", "timestamp", "tx_hash",
            name="uq_token_lifecycle_identity",
        ),
    )
    op.create_index("ix_token_lifecycle_events_chain", "token_lifecycle_events", ["chain"])
    op.create_index("ix_token_lifecycle_events_launchpad", "token_lifecycle_events", ["launchpad"])
    op.create_index("ix_token_lifecycle_events_token_address", "token_lifecycle_events", ["token_address"])
    op.create_index("ix_token_lifecycle_events_timestamp", "token_lifecycle_events", ["timestamp"])
    op.create_index("ix_token_lifecycle_events_token_time", "token_lifecycle_events", ["token_address", "timestamp"])
    op.create_index("ix_token_lifecycle_events_launchpad_time", "token_lifecycle_events", ["launchpad", "timestamp"])

    op.create_table(
        "wallet_token_performance",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chain", sa.String(32), nullable=False),
        sa.Column("launchpad", sa.String(64)),
        sa.Column("wallet_address", sa.String(128), nullable=False),
        sa.Column("token_address", sa.String(128), nullable=False),
        sa.Column("entry_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("entry_market_cap", decimal_18),
        sa.Column("entry_price", decimal_18),
        sa.Column("position_size", decimal_18),
        sa.Column("total_buy", decimal_18),
        sa.Column("total_sell", decimal_18),
        sa.Column("realized_pnl", decimal_18),
        sa.Column("unrealized_pnl", decimal_18),
        sa.Column("max_pnl", decimal_18),
        sa.Column("max_drawdown", decimal_18),
        sa.Column("exit_time", sa.DateTime(timezone=True)),
        sa.Column("holding_duration_seconds", sa.BigInteger()),
        sa.Column("roi", decimal_18),
        sa.Column("multiple", decimal_18),
        sa.Column("reached_2x", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reached_5x", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reached_10x", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reached_50x", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reached_100x", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("chain", "wallet_address", "token_address", name="uq_wallet_token_performance_pair"),
    )
    op.create_index("ix_wallet_token_performance_chain", "wallet_token_performance", ["chain"])
    op.create_index("ix_wallet_token_performance_launchpad", "wallet_token_performance", ["launchpad"])
    op.create_index("ix_wallet_token_performance_wallet_address", "wallet_token_performance", ["wallet_address"])
    op.create_index("ix_wallet_token_performance_token_address", "wallet_token_performance", ["token_address"])
    op.create_index("ix_wallet_token_performance_wallet_time", "wallet_token_performance", ["wallet_address", "entry_time"])
    op.create_index("ix_wallet_token_performance_token_time", "wallet_token_performance", ["token_address", "entry_time"])

    op.create_table(
        "wallet_performance",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chain", sa.String(32), nullable=False),
        sa.Column("wallet_address", sa.String(128), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tokens_traded", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("winning_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("losing_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("win_rate", score),
        sa.Column("avg_roi", decimal_18),
        sa.Column("median_roi", decimal_18),
        sa.Column("avg_multiple", decimal_18),
        sa.Column("median_multiple", decimal_18),
        sa.Column("total_realized_pnl", decimal_18),
        sa.Column("max_multiple", decimal_18),
        sa.Column("early_entry_rate", score),
        sa.Column("graduation_rate", score),
        sa.Column("ath_capture_rate", score),
        sa.Column("rug_exposure", score),
        sa.Column("bot_probability", sa.Numeric(4, 3)),
        sa.Column("smart_money_score", score),
        sa.UniqueConstraint("chain", "wallet_address", "as_of", name="uq_wallet_performance_snapshot"),
    )
    op.create_index("ix_wallet_performance_chain", "wallet_performance", ["chain"])
    op.create_index("ix_wallet_performance_wallet_address", "wallet_performance", ["wallet_address"])
    op.create_index("ix_wallet_performance_as_of", "wallet_performance", ["as_of"])
    op.create_index("ix_wallet_performance_wallet_time", "wallet_performance", ["wallet_address", "as_of"])

    op.create_table(
        "smart_money_scores",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chain", sa.String(32), nullable=False),
        sa.Column("wallet_address", sa.String(128), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("score", score, nullable=False),
        sa.Column("penalties", score, nullable=False, server_default="0"),
        sa.Column("components_json", sa.JSON(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("version", sa.String(32), nullable=False, server_default="v1"),
        sa.UniqueConstraint("chain", "wallet_address", "as_of", "version", name="uq_smart_money_score_snapshot"),
    )
    op.create_index("ix_smart_money_scores_chain", "smart_money_scores", ["chain"])
    op.create_index("ix_smart_money_scores_wallet_address", "smart_money_scores", ["wallet_address"])
    op.create_index("ix_smart_money_scores_as_of", "smart_money_scores", ["as_of"])
    op.create_index("ix_smart_money_scores_wallet_time", "smart_money_scores", ["wallet_address", "as_of"])


def downgrade() -> None:
    op.drop_table("smart_money_scores")
    op.drop_table("wallet_performance")
    op.drop_table("wallet_token_performance")
    op.drop_table("token_lifecycle_events")
    op.drop_table("trade_events")
    op.drop_index("ix_tokens_creator_address", table_name="tokens")
    op.drop_index("ix_tokens_launchpad", table_name="tokens")
    for name in (
        "launchpad", "creator_address", "decimals", "total_supply", "first_trade_at",
        "graduation_at", "ath_at", "ath_price", "graduation_liquidity", "status", "outcome_class",
    ):
        op.drop_column("tokens", name)


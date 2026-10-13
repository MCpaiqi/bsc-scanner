"""Add versioned point-in-time intelligence snapshots and query indexes.

Revision ID: 20260911_0010
Revises: 20260911_0009

The indexes mirror production reads: canonical events are filtered by chain and
block/time, wallet snapshots are ranked by score at a versioned cutoff, and
token snapshots are fetched by token/version at or before a cutoff.
"""

import sqlalchemy as sa
from alembic import op

revision = "20260911_0010"
down_revision = "20260911_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_trade_events_chain_block", "trade_events", ["chain", "block_number"]
    )
    op.create_index(
        "ix_market_events_token_time", "market_events", ["token_address", "timestamp"]
    )
    op.create_index(
        "ix_market_events_pair_time", "market_events", ["pair_address", "timestamp"]
    )
    op.create_index(
        "ix_market_events_chain_block", "market_events", ["chain_id", "block_number"]
    )

    op.create_table(
        "wallet_smart_money_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chain", sa.String(32), nullable=False),
        sa.Column("wallet_address", sa.String(128), nullable=False),
        sa.Column("score", sa.Numeric(6, 2), nullable=False),
        sa.Column("confidence", sa.String(16), nullable=False),
        sa.Column("confidence_score", sa.Numeric(6, 2), nullable=False),
        sa.Column("score_version", sa.String(32), nullable=False),
        sa.Column("profile_version", sa.String(32), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sample_size", sa.Integer(), nullable=False),
        sa.Column("data_completeness", sa.Numeric(5, 4), nullable=False),
        sa.Column("historical_coverage", sa.Numeric(5, 4), nullable=False),
        sa.Column("trade_quality", sa.Numeric(5, 4), nullable=False),
        sa.Column("profile_json", sa.JSON(), nullable=False),
        sa.Column("components_json", sa.JSON(), nullable=False),
        sa.Column("contributions_json", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "chain", "wallet_address", "as_of", "score_version", "profile_version",
            name="uq_wallet_smart_money_snapshot_version",
        ),
    )
    op.create_index(
        "ix_wallet_smart_money_snapshot_lookup",
        "wallet_smart_money_snapshots",
        ["chain", "wallet_address", "score_version", "as_of"],
    )
    op.create_index(
        "ix_wallet_smart_money_snapshot_rank",
        "wallet_smart_money_snapshots",
        ["chain", "score_version", "as_of", "score"],
    )

    op.create_table(
        "token_smart_money_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chain", sa.String(32), nullable=False),
        sa.Column("token_address", sa.String(128), nullable=False),
        sa.Column("smart_wallet_count", sa.Integer(), nullable=False),
        sa.Column("smart_money_buy_usd", sa.Numeric(38, 18), nullable=False),
        sa.Column("smart_money_sell_usd", sa.Numeric(38, 18), nullable=False),
        sa.Column("smart_money_net_flow", sa.Numeric(38, 18), nullable=False),
        sa.Column("early_smart_buy_count", sa.Integer(), nullable=False),
        sa.Column("smart_money_score", sa.Numeric(6, 2)),
        sa.Column("confidence", sa.String(16), nullable=False),
        sa.Column("confidence_score", sa.Numeric(6, 2), nullable=False),
        sa.Column("data_status", sa.String(32), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("score_version", sa.String(32), nullable=False),
        sa.Column("top_smart_wallets_json", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "chain", "token_address", "as_of", "score_version",
            name="uq_token_smart_money_snapshot_version",
        ),
    )
    op.create_index(
        "ix_token_smart_money_snapshot_lookup",
        "token_smart_money_snapshots",
        ["chain", "token_address", "score_version", "as_of"],
    )
    op.create_index(
        "ix_token_smart_money_snapshot_rank",
        "token_smart_money_snapshots",
        ["chain", "score_version", "as_of", "smart_money_score"],
    )


def downgrade() -> None:
    op.drop_table("token_smart_money_snapshots")
    op.drop_table("wallet_smart_money_snapshots")
    op.drop_index("ix_market_events_chain_block", table_name="market_events")
    op.drop_index("ix_market_events_pair_time", table_name="market_events")
    op.drop_index("ix_market_events_token_time", table_name="market_events")
    op.drop_index("ix_trade_events_chain_block", table_name="trade_events")

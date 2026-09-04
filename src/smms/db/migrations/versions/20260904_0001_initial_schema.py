"""Create the immutable Phase 1 core schema.

Revision ID: 20260904_0001
Revises:
Create Date: 2026-09-04
"""

import sqlalchemy as sa
from alembic import op

revision = "20260904_0001"
down_revision = None
branch_labels = None
depends_on = None

decimal_18 = sa.Numeric(38, 18)
raw_amount = sa.Numeric(50, 0)
score = sa.Numeric(6, 2)


def upgrade() -> None:
    op.create_table(
        "tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("address", sa.String(128), nullable=False),
        sa.Column("symbol", sa.String(32)),
        sa.Column("name", sa.String(128)),
        sa.Column("chain", sa.String(32), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True)),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("initial_liquidity", decimal_18),
        sa.Column("current_liquidity", decimal_18),
        sa.Column("initial_market_cap", decimal_18),
        sa.Column("current_market_cap", decimal_18),
        sa.Column("ath_market_cap", decimal_18),
        sa.Column("ath_multiple", decimal_18),
        sa.Column("volume_5m", decimal_18),
        sa.Column("volume_1h", decimal_18),
        sa.Column("volume_24h", decimal_18),
        sa.Column("holder_count", sa.Integer()),
        sa.Column("top_holder_concentration", score),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("chain", "address", name="uq_tokens_chain_address"),
    )
    op.create_index("ix_tokens_address", "tokens", ["address"])

    op.create_table(
        "wallets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("address", sa.String(128), nullable=False),
        sa.Column("chain", sa.String(32), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True)),
        sa.Column("last_active_at", sa.DateTime(timezone=True)),
        sa.Column("total_transactions", sa.Integer(), nullable=False),
        sa.Column("total_swaps", sa.Integer(), nullable=False),
        sa.Column("win_rate", score),
        sa.Column("realized_pnl", decimal_18),
        sa.Column("unrealized_pnl", decimal_18),
        sa.Column("max_multiple", decimal_18),
        sa.Column("median_multiple", decimal_18),
        sa.Column("bot_probability", sa.Numeric(4, 3)),
        sa.Column("smart_money_score", score),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("chain", "address", name="uq_wallets_chain_address"),
    )
    op.create_index("ix_wallets_address", "wallets", ["address"])

    op.create_table(
        "wallet_transactions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "wallet_id",
            sa.Integer(),
            sa.ForeignKey("wallets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("signature", sa.String(128), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("token_in", sa.String(128)),
        sa.Column("token_out", sa.String(128)),
        sa.Column("amount_in_raw", raw_amount),
        sa.Column("amount_out_raw", raw_amount),
        sa.Column("amount_in", decimal_18),
        sa.Column("amount_out", decimal_18),
        sa.Column("usd_value", decimal_18),
        sa.Column("side", sa.String(8)),
        sa.Column("dex", sa.String(64)),
        sa.Column("slot", sa.BigInteger()),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.UniqueConstraint(
            "wallet_id", "signature", name="uq_wallet_transactions_wallet_signature"
        ),
    )
    op.create_index("ix_wallet_transactions_signature", "wallet_transactions", ["signature"])
    op.create_index("ix_wallet_transactions_timestamp", "wallet_transactions", ["timestamp"])

    op.create_table(
        "token_wallet_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "token_id",
            sa.Integer(),
            sa.ForeignKey("tokens.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "wallet_id",
            sa.Integer(),
            sa.ForeignKey("wallets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_type", sa.String(16), nullable=False),
        sa.Column("price", decimal_18),
        sa.Column("amount_raw", raw_amount),
        sa.Column("amount", decimal_18),
        sa.Column("usd_value", decimal_18),
        sa.Column("market_cap", decimal_18),
        sa.Column("liquidity", decimal_18),
        sa.Column("tx_signature", sa.String(128)),
    )
    op.create_index(
        "ix_token_wallet_events_token_time", "token_wallet_events", ["token_id", "timestamp"]
    )
    op.create_index(
        "ix_token_wallet_events_wallet_time", "token_wallet_events", ["wallet_id", "timestamp"]
    )
    op.create_index(
        "ix_token_wallet_events_tx_signature", "token_wallet_events", ["tx_signature"]
    )

    op.create_table(
        "wallet_scores",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "wallet_id",
            sa.Integer(),
            sa.ForeignKey("wallets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("historical_return_score", score),
        sa.Column("win_rate_score", score),
        sa.Column("early_entry_score", score),
        sa.Column("consistency_score", score),
        sa.Column("activity_score", score),
        sa.Column("bot_penalty", score),
        sa.Column("total_score", score),
        sa.Column("version", sa.String(32), nullable=False),
        sa.UniqueConstraint(
            "wallet_id", "timestamp", "version", name="uq_wallet_scores_snapshot"
        ),
    )
    op.create_index("ix_wallet_scores_wallet_time", "wallet_scores", ["wallet_id", "timestamp"])

    op.create_table(
        "token_scores",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "token_id",
            sa.Integer(),
            sa.ForeignKey("tokens.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("wallet_consensus_score", score),
        sa.Column("wallet_quality_score", score),
        sa.Column("entry_timing_score", score),
        sa.Column("liquidity_score", score),
        sa.Column("holder_score", score),
        sa.Column("volume_score", score),
        sa.Column("market_score", score),
        sa.Column("risk_score", score),
        sa.Column("total_score", score),
        sa.Column("version", sa.String(32), nullable=False),
        sa.UniqueConstraint(
            "token_id", "timestamp", "version", name="uq_token_scores_snapshot"
        ),
    )
    op.create_index("ix_token_scores_token_time", "token_scores", ["token_id", "timestamp"])

    op.create_table(
        "signals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "token_id",
            sa.Integer(),
            sa.ForeignKey("tokens.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("signal_type", sa.String(16), nullable=False),
        sa.Column("signal_strength", score),
        sa.Column("wallet_count", sa.Integer(), nullable=False),
        sa.Column("weighted_wallet_count", decimal_18),
        sa.Column("consensus_score", score),
        sa.Column("token_score", score),
        sa.Column("risk_score", score),
        sa.Column("entry_price", decimal_18),
        sa.Column("market_cap", decimal_18),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
    )
    op.create_index("ix_signals_token_id", "signals", ["token_id"])
    op.create_index("ix_signals_created_at", "signals", ["created_at"])

    op.create_table(
        "paper_trades",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "token_id",
            sa.Integer(),
            sa.ForeignKey("tokens.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("signal_id", sa.Integer(), sa.ForeignKey("signals.id", ondelete="SET NULL")),
        sa.Column("entry_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("entry_price", decimal_18, nullable=False),
        sa.Column("exit_time", sa.DateTime(timezone=True)),
        sa.Column("exit_price", decimal_18),
        sa.Column("quantity", decimal_18, nullable=False),
        sa.Column("capital", decimal_18, nullable=False),
        sa.Column("fees", decimal_18, nullable=False),
        sa.Column("slippage", decimal_18, nullable=False),
        sa.Column("realized_pnl", decimal_18),
        sa.Column("return_pct", score),
        sa.Column("exit_reason", sa.String(64)),
    )
    op.create_index("ix_paper_trades_token_id", "paper_trades", ["token_id"])

    op.create_table(
        "raw_chain_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("record_type", sa.String(64), nullable=False),
        sa.Column("external_id", sa.String(256), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("normalized_at", sa.DateTime(timezone=True)),
        sa.Column("checksum", sa.String(128)),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.UniqueConstraint(
            "provider", "record_type", "external_id", name="uq_raw_chain_record_identity"
        ),
    )
    op.create_index("ix_raw_chain_records_fetched_at", "raw_chain_records", ["fetched_at"])

    op.create_table(
        "ingestion_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("records_seen", sa.Integer(), nullable=False),
        sa.Column("records_written", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.Text()),
    )


def downgrade() -> None:
    op.drop_table("ingestion_runs")
    op.drop_table("raw_chain_records")
    op.drop_table("paper_trades")
    op.drop_table("signals")
    op.drop_table("token_scores")
    op.drop_table("wallet_scores")
    op.drop_table("token_wallet_events")
    op.drop_table("wallet_transactions")
    op.drop_table("wallets")
    op.drop_table("tokens")


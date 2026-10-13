"""Add canonical BSC trade fields and materialized market snapshots.

Revision ID: 20260913_0023
Revises: 20260913_0022
"""

import sqlalchemy as sa
from alembic import op

revision = "20260913_0023"
down_revision = "20260913_0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name, column in (
        ("chain_id", sa.Column("chain_id", sa.Integer(), nullable=True)),
        ("pair_address", sa.Column("pair_address", sa.String(128), nullable=True)),
        ("base_token_address", sa.Column("base_token_address", sa.String(128), nullable=True)),
        ("block_hash", sa.Column("block_hash", sa.String(128), nullable=True)),
        ("token_decimals", sa.Column("token_decimals", sa.Integer(), nullable=True)),
        ("base_decimals", sa.Column("base_decimals", sa.Integer(), nullable=True)),
        ("price_usd", sa.Column("price_usd", sa.Numeric(38, 18), nullable=True)),
        ("value_usd", sa.Column("value_usd", sa.Numeric(38, 18), nullable=True)),
        ("canonical", sa.Column("canonical", sa.Boolean(), nullable=False, server_default=sa.true())),
        ("removed", sa.Column("removed", sa.Boolean(), nullable=False, server_default=sa.false())),
    ):
        op.add_column("trade_events", column)
        if name in {"chain_id", "pair_address", "base_token_address", "block_hash"}:
            op.create_index(f"ix_trade_events_{name}", "trade_events", [name])

    op.create_table(
        "token_market_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chain", sa.String(32), nullable=False),
        sa.Column("chain_id", sa.Integer(), nullable=False),
        sa.Column("token_address", sa.String(128), nullable=False),
        sa.Column("pair_address", sa.String(128), nullable=False),
        sa.Column("dex", sa.String(64)),
        *[sa.Column(name, sa.Numeric(38, 18)) for name in (
            "price_usd", "market_cap_usd", "fdv_usd", "liquidity_usd", "volume_1m",
            "volume_5m", "volume_h1", "volume_h24", "volume_token_1m",
            "volume_token_5m", "price_change_5m", "price_change_h1",
        )],
        *[sa.Column(name, sa.Integer()) for name in (
            "buys_1m", "sells_1m", "buys_5m", "sells_5m", "unique_buyers_1m",
            "unique_buyers_5m",
        )],
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("freshness_seconds", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(32), nullable=False, server_default="KNOWN"),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "chain", "token_address", "pair_address", "source", "as_of",
            name="uq_token_market_snapshot_identity",
        ),
    )
    op.create_index("ix_token_market_snapshot_lookup", "token_market_snapshots", ["chain", "token_address", "as_of"])
    op.create_index("ix_token_market_snapshot_pair_time", "token_market_snapshots", ["chain", "pair_address", "as_of"])


def downgrade() -> None:
    op.drop_table("token_market_snapshots")
    for name in ("block_hash", "base_token_address", "pair_address", "chain_id"):
        op.drop_index(f"ix_trade_events_{name}", table_name="trade_events")
    for name in ("removed", "canonical", "value_usd", "price_usd", "base_decimals", "token_decimals", "block_hash", "base_token_address", "pair_address", "chain_id"):
        op.drop_column("trade_events", name)

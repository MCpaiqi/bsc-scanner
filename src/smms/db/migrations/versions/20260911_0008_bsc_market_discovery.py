"""Add BSC market discovery tables.

Revision ID: 20260911_0008
Revises: 20260911_0007
"""

import sqlalchemy as sa
from alembic import op

revision = "20260911_0008"
down_revision = "20260911_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tokens", sa.Column("creation_tx_hash", sa.String(length=128)))
    op.add_column("tokens", sa.Column("creation_block", sa.BigInteger()))
    op.add_column("tokens", sa.Column("creation_timestamp", sa.DateTime(timezone=True)))
    op.create_index("ix_tokens_creation_tx_hash", "tokens", ["creation_tx_hash"])
    op.create_index("ix_tokens_creation_block", "tokens", ["creation_block"])
    op.create_table(
        "pairs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chain_id", sa.Integer(), nullable=False),
        sa.Column("address", sa.String(length=128), nullable=False),
        sa.Column("token0_address", sa.String(length=128), nullable=False),
        sa.Column("token1_address", sa.String(length=128), nullable=False),
        sa.Column("factory_address", sa.String(length=128)),
        sa.Column("dex", sa.String(length=64), nullable=False),
        sa.Column("quote_token", sa.String(length=128)),
        sa.Column("creation_tx_hash", sa.String(length=128)),
        sa.Column("creation_block", sa.BigInteger()),
        sa.Column("creation_timestamp", sa.DateTime(timezone=True)),
        sa.Column("current_liquidity", sa.Numeric(38, 18)),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.UniqueConstraint("chain_id", "address", name="uq_pairs_chain_address"),
    )
    op.create_index("ix_pairs_chain_id", "pairs", ["chain_id"])
    op.create_index("ix_pairs_address", "pairs", ["address"])
    op.create_index("ix_pairs_token0_token1", "pairs", ["token0_address", "token1_address"])
    op.create_table(
        "market_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chain_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("token_address", sa.String(length=128)),
        sa.Column("pair_address", sa.String(length=128)),
        sa.Column("wallet_address", sa.String(length=128)),
        sa.Column("tx_hash", sa.String(length=128), nullable=False),
        sa.Column("event_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("block_number", sa.BigInteger()),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("side", sa.String(length=16)),
        sa.Column("token_amount", sa.Numeric(38, 18)),
        sa.Column("quote_amount", sa.Numeric(38, 18)),
        sa.Column("usd_value", sa.Numeric(38, 18)),
        sa.Column("price", sa.Numeric(38, 18)),
        sa.Column("source", sa.String(length=128), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4)),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.UniqueConstraint("chain_id", "tx_hash", "event_index", name="uq_market_events_identity"),
    )


def downgrade() -> None:
    op.drop_table("market_events")
    op.drop_table("pairs")
    op.drop_index("ix_tokens_creation_block", table_name="tokens")
    op.drop_index("ix_tokens_creation_tx_hash", table_name="tokens")
    op.drop_column("tokens", "creation_timestamp")
    op.drop_column("tokens", "creation_block")
    op.drop_column("tokens", "creation_tx_hash")

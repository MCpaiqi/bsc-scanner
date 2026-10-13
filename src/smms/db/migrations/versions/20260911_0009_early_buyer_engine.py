"""Add replayable early buyer and wallet/token position snapshots."""
import sqlalchemy as sa
from alembic import op

revision = "20260911_0009"
down_revision = "20260911_0008"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table("early_buyers",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("chain", sa.String(32), nullable=False),
        sa.Column("wallet_address", sa.String(128), nullable=False), sa.Column("token_address", sa.String(128), nullable=False),
        sa.Column("first_buy_tx", sa.String(128), nullable=False), sa.Column("first_buy_block", sa.BigInteger()),
        sa.Column("first_buy_timestamp", sa.DateTime(timezone=True), nullable=False), sa.Column("first_buy_price", sa.Numeric(38,18)),
        sa.Column("first_buy_usd", sa.Numeric(38,18)), sa.Column("first_buy_market_cap", sa.Numeric(38,18)), sa.Column("market_cap_status", sa.String(16), nullable=False, server_default="UNKNOWN"),
        sa.Column("buy_rank", sa.Integer(), nullable=False), sa.Column("buy_count", sa.Integer(), nullable=False, server_default="1"), sa.Column("total_buy_usd", sa.Numeric(38,18)),
        sa.Column("entry_delay_seconds", sa.BigInteger()), sa.Column("entry_time_bucket", sa.String(16)), sa.Column("window_type", sa.String(16), nullable=False, server_default="buyers"), sa.Column("window_value", sa.Integer(), nullable=False, server_default="20"), sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.UniqueConstraint("chain", "wallet_address", "token_address", name="uq_early_buyers_wallet_token"))
    op.create_index("ix_early_buyers_token_rank", "early_buyers", ["token_address", "buy_rank"])
    op.create_table("wallet_token_positions",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("chain", sa.String(32), nullable=False), sa.Column("wallet_address", sa.String(128), nullable=False), sa.Column("token_address", sa.String(128), nullable=False),
        sa.Column("cumulative_bought", sa.Numeric(38,18), nullable=False, server_default="0"), sa.Column("cumulative_sold", sa.Numeric(38,18), nullable=False, server_default="0"), sa.Column("current_balance", sa.Numeric(38,18), nullable=False, server_default="0"),
        sa.Column("total_invested", sa.Numeric(38,18)), sa.Column("total_returned", sa.Numeric(38,18)), sa.Column("realized_pnl", sa.Numeric(38,18)), sa.Column("unrealized_pnl", sa.Numeric(38,18)), sa.Column("roi", sa.Numeric(38,18)), sa.Column("current_price", sa.Numeric(38,18)), sa.Column("current_market_cap", sa.Numeric(38,18)), sa.Column("last_trade_timestamp", sa.DateTime(timezone=True)), sa.Column("last_trade_block", sa.BigInteger()), sa.Column("is_reorg_corrected", sa.Boolean(), nullable=False, server_default=sa.false()), sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.UniqueConstraint("chain", "wallet_address", "token_address", name="uq_wallet_token_positions_pair"))
    op.create_index("ix_wallet_token_positions_token", "wallet_token_positions", ["token_address"])

def downgrade() -> None:
    op.drop_table("wallet_token_positions"); op.drop_index("ix_early_buyers_token_rank", table_name="early_buyers"); op.drop_table("early_buyers")

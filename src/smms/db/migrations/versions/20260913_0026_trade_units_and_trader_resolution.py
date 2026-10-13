"""Add explicit BSC trade units and trader-resolution provenance.

Revision ID: 20260913_0026
Revises: 20260913_0025
"""

import sqlalchemy as sa
from alembic import op

revision = "20260913_0026"
down_revision = "20260913_0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name, column in (
        ("quote_amount", sa.Column("quote_amount", sa.Numeric(38, 18))),
        ("quote_decimals", sa.Column("quote_decimals", sa.Integer())),
        ("price_quote", sa.Column("price_quote", sa.Numeric(38, 18))),
        ("value_quote", sa.Column("value_quote", sa.Numeric(38, 18))),
        ("swap_sender", sa.Column("swap_sender", sa.String(128))),
        ("swap_recipient", sa.Column("swap_recipient", sa.String(128))),
        ("transaction_from", sa.Column("transaction_from", sa.String(128))),
        ("transaction_to", sa.Column("transaction_to", sa.String(128))),
        ("trader_resolution", sa.Column("trader_resolution", sa.String(32), nullable=False, server_default="UNKNOWN")),
        ("trader_confidence", sa.Column("trader_confidence", sa.String(16), nullable=False, server_default="UNKNOWN")),
    ):
        op.add_column("trade_events", column)


def downgrade() -> None:
    for name in (
        "trader_confidence", "trader_resolution", "transaction_to", "transaction_from",
        "swap_recipient", "swap_sender", "value_quote", "price_quote", "quote_decimals", "quote_amount",
    ):
        op.drop_column("trade_events", name)

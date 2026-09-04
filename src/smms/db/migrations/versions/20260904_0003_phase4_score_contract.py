"""Complete the Phase 4 score and trade-event contracts.

Revision ID: 20260904_0003
Revises: 20260904_0002
"""

import sqlalchemy as sa
from alembic import op

revision = "20260904_0003"
down_revision = "20260904_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "wallet_performance",
        sa.Column("risk_adjusted_return", sa.Numeric(38, 18)),
    )
    with op.batch_alter_table("trade_events") as batch_op:
        batch_op.create_check_constraint(
            "ck_trade_events_side_buy_sell",
            "side IN ('BUY', 'SELL')",
        )


def downgrade() -> None:
    with op.batch_alter_table("trade_events") as batch_op:
        batch_op.drop_constraint("ck_trade_events_side_buy_sell", type_="check")
    op.drop_column("wallet_performance", "risk_adjusted_return")

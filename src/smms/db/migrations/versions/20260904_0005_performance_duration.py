"""Add the canonical holding-duration field to token performance.

Revision ID: 20260904_0005
Revises: 20260904_0004
"""

import sqlalchemy as sa
from alembic import op

revision = "20260904_0005"
down_revision = "20260904_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "wallet_token_performance",
        sa.Column("holding_duration", sa.BigInteger()),
    )


def downgrade() -> None:
    op.drop_column("wallet_token_performance", "holding_duration")


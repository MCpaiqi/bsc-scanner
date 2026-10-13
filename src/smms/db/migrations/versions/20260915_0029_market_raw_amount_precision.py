"""Store raw BSC swap amounts at uint-scale precision.

Revision ID: 20260915_0029
Revises: 20260914_0028
"""

import sqlalchemy as sa
from alembic import op

revision = "20260915_0029"
down_revision = "20260914_0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # MarketEvent intentionally retains decoder raw units. The previous
    # NUMERIC(38,18) type overflows for legitimate high-supply token amounts.
    with op.batch_alter_table("market_events") as batch:
        batch.alter_column(
            "token_amount",
            existing_type=sa.Numeric(38, 18),
            type_=sa.Numeric(50, 0),
            existing_nullable=True,
        )
        batch.alter_column(
            "quote_amount",
            existing_type=sa.Numeric(38, 18),
            type_=sa.Numeric(50, 0),
            existing_nullable=True,
        )


def downgrade() -> None:
    with op.batch_alter_table("market_events") as batch:
        batch.alter_column(
            "quote_amount",
            existing_type=sa.Numeric(50, 0),
            type_=sa.Numeric(38, 18),
            existing_nullable=True,
        )
        batch.alter_column(
            "token_amount",
            existing_type=sa.Numeric(50, 0),
            type_=sa.Numeric(38, 18),
            existing_nullable=True,
        )

"""Add generic token volume and holder aliases for chain-neutral reads.

Revision ID: 20260904_0004
Revises: 20260904_0003
"""

import sqlalchemy as sa
from alembic import op

revision = "20260904_0004"
down_revision = "20260904_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tokens", sa.Column("volume", sa.Numeric(38, 18)))
    op.add_column("tokens", sa.Column("holders", sa.Integer()))


def downgrade() -> None:
    op.drop_column("tokens", "holders")
    op.drop_column("tokens", "volume")


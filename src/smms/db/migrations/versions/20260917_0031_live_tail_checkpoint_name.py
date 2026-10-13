"""Allow a live-tail pair checkpoint to retain its full lane name."""

import sqlalchemy as sa
from alembic import op

revision = "20260917_0031"
down_revision = "20260917_0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("stream_checkpoints") as batch:
        batch.alter_column(
            "stream_name",
            existing_type=sa.String(length=64),
            type_=sa.String(length=128),
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("stream_checkpoints") as batch:
        batch.alter_column(
            "stream_name",
            existing_type=sa.String(length=128),
            type_=sa.String(length=64),
            existing_nullable=False,
        )

"""Track canonical fork provenance for PairCreated discoveries."""

import sqlalchemy as sa
from alembic import op

revision = "20260914_0028"
down_revision = "20260913_0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("pairs") as batch:
        batch.add_column(sa.Column("creation_block_hash", sa.String(128), nullable=True))
        batch.add_column(sa.Column("canonical", sa.Boolean(), nullable=False, server_default=sa.true()))
        batch.add_column(sa.Column("removed", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.create_index("ix_pairs_creation_block_hash", ["creation_block_hash"])
    with op.batch_alter_table("pairs") as batch:
        batch.alter_column("canonical", server_default=None)
        batch.alter_column("removed", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("pairs") as batch:
        batch.drop_index("ix_pairs_creation_block_hash")
        batch.drop_column("removed")
        batch.drop_column("canonical")
        batch.drop_column("creation_block_hash")

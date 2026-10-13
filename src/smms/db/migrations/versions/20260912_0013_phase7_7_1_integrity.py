"""Persist Phase 7.7.1 reproducibility metadata."""
import sqlalchemy as sa
from alembic import op

revision = "20260912_0013"
down_revision = "20260911_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("signals", sa.Column("config_version", sa.String(32), nullable=False, server_default="config-v1"))
    op.add_column("signals", sa.Column("config_hash", sa.String(64), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("signals", "config_hash")
    op.drop_column("signals", "config_version")

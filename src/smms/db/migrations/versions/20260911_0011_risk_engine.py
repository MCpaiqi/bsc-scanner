"""Add Phase 7.6 token risk snapshots."""
import sqlalchemy as sa
from alembic import op

revision = "20260911_0011"
down_revision = "20260911_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "token_risk_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chain", sa.String(32), nullable=False),
        sa.Column("token_address", sa.String(128), nullable=False),
        sa.Column("contract_risk", sa.String(16), nullable=False),
        sa.Column("liquidity_risk", sa.String(16), nullable=False),
        sa.Column("holder_risk", sa.String(16), nullable=False),
        sa.Column("behavior_risk", sa.String(16), nullable=False),
        sa.Column("bot_probability", sa.Numeric(4, 3)),
        sa.Column("wash_probability", sa.Numeric(4, 3)),
        sa.Column("overall_risk", sa.String(16), nullable=False),
        sa.Column("confidence", sa.String(16), nullable=False),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column("dimensions_json", sa.JSON(), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("risk_version", sa.String(32), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("chain", "token_address", "as_of", "risk_version", name="uq_token_risk_snapshot_version"),
    )
    op.create_index("ix_token_risk_snapshot_lookup", "token_risk_snapshots", ["chain", "token_address", "risk_version", "as_of"])
    op.create_index("ix_token_risk_snapshot_rank", "token_risk_snapshots", ["chain", "risk_version", "as_of", "overall_risk"])


def downgrade() -> None:
    op.drop_table("token_risk_snapshots")

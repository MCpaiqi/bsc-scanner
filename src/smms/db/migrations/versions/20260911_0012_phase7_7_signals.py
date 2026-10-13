"""Add Phase 7.7 point-in-time signal fields."""
import sqlalchemy as sa
from alembic import op

revision = "20260911_0012"
down_revision = "20260911_0011"
branch_labels = None
depends_on = None

def upgrade() -> None:
    for name, column in (
        ("confidence", sa.Column("confidence", sa.Numeric(6, 2))),
        ("smart_money_score", sa.Column("smart_money_score", sa.Numeric(6, 2))),
        ("risk_level", sa.Column("risk_level", sa.String(16))),
        ("market_score", sa.Column("market_score", sa.Numeric(6, 2))),
        ("entry_market_cap", sa.Column("entry_market_cap", sa.Numeric(38, 18))),
        ("entry_liquidity", sa.Column("entry_liquidity", sa.Numeric(38, 18))),
        ("entry_reasons", sa.Column("entry_reasons", sa.JSON(), nullable=False, server_default="[]")),
        ("risk_flags", sa.Column("risk_flags", sa.JSON(), nullable=False, server_default="[]")),
        ("data_status", sa.Column("data_status", sa.String(32), nullable=False, server_default="OK")),
        ("model_version", sa.Column("model_version", sa.String(32), nullable=False, server_default="signal-v1")),
    ):
        op.add_column("signals", column)
    with op.batch_alter_table("signals") as batch_op:
        batch_op.create_unique_constraint("uq_signals_token_time_version", ["token_id", "created_at", "model_version"])
    op.create_index("ix_signals_created_model", "signals", ["created_at", "model_version"])

def downgrade() -> None:
    op.drop_index("ix_signals_created_model", table_name="signals")
    with op.batch_alter_table("signals") as batch_op:
        batch_op.drop_constraint("uq_signals_token_time_version", type_="unique")
    for name in ("model_version", "data_status", "risk_flags", "entry_reasons", "entry_liquidity", "entry_market_cap", "market_score", "risk_level", "smart_money_score", "confidence"):
        op.drop_column("signals", name)

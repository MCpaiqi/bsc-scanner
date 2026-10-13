"""Phase 7.9.1 live signal shadow outcomes."""
import sqlalchemy as sa
from alembic import op

revision = "20260912_0017"
down_revision = "20260912_0016"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table(
        "live_signal_observations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("candidate_id", sa.Integer(), sa.ForeignKey("radar_candidates.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_id", sa.Integer(), sa.ForeignKey("tokens.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_address", sa.String(128), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("signal_class", sa.String(32), nullable=False),
        sa.Column("signal_score", sa.Numeric(8, 3)),
        sa.Column("price", sa.Numeric(38, 18)),
        sa.Column("market_cap", sa.Numeric(38, 18)),
        sa.Column("liquidity", sa.Numeric(38, 18)),
        sa.Column("smart_money_snapshot_id", sa.Integer(), sa.ForeignKey("token_smart_money_snapshots.id", ondelete="SET NULL")),
        sa.Column("risk_snapshot_id", sa.Integer(), sa.ForeignKey("token_risk_snapshots.id", ondelete="SET NULL")),
        sa.Column("feature_version", sa.String(64), nullable=False),
        sa.Column("config_version", sa.String(64), nullable=False),
        sa.Column("model_version", sa.String(64), nullable=False),
        sa.Column("sample_group", sa.String(32), nullable=False),
        sa.Column("t0_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("candidate_id", "signal_class", name="uq_live_observation_candidate_class"),
    )
    op.create_index("ix_live_observations_token_time", "live_signal_observations", ["token_address", "observed_at"])
    op.create_index("ix_live_observations_group", "live_signal_observations", ["sample_group", "observed_at"])
    op.create_table(
        "live_signal_outcomes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("observation_id", sa.Integer(), sa.ForeignKey("live_signal_observations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("horizon", sa.String(8), nullable=False),
        sa.Column("target_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True)),
        sa.Column("return_pct", sa.Numeric(12, 6)),
        sa.Column("max_return_pct", sa.Numeric(12, 6)),
        sa.Column("max_drawdown_pct", sa.Numeric(12, 6)),
        sa.Column("target_hit", sa.Boolean()),
        sa.Column("stop_hit", sa.Boolean()),
        sa.Column("rug", sa.Boolean()),
        sa.Column("liquidity_failure", sa.Boolean()),
        sa.Column("endpoint_price", sa.Numeric(38, 18)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error_code", sa.String(32)),
        sa.UniqueConstraint("observation_id", "horizon", name="uq_live_outcome_observation_horizon"),
    )
    op.create_index("ix_live_outcomes_horizon_status", "live_signal_outcomes", ["horizon", "status"])

def downgrade() -> None:
    op.drop_index("ix_live_outcomes_horizon_status", table_name="live_signal_outcomes")
    op.drop_table("live_signal_outcomes")
    op.drop_index("ix_live_observations_group", table_name="live_signal_observations")
    op.drop_index("ix_live_observations_token_time", table_name="live_signal_observations")
    op.drop_table("live_signal_observations")

"""Version Forward outcomes and persist bounded endpoint/retry provenance."""

import sqlalchemy as sa
from alembic import context, op

revision = "20261105_0035"
down_revision = "20260919_0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "forward_evaluation_cursors",
        sa.Column("evaluation_version", sa.String(64), primary_key=True),
        sa.Column("last_observation_id", sa.Integer(), nullable=False, server_default="0"),
    )
    with op.batch_alter_table("live_signal_outcomes") as batch:
        batch.add_column(sa.Column("evaluation_version", sa.String(64), nullable=False,
                                   server_default="legacy-v1"))
        batch.add_column(sa.Column("next_evaluation_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("endpoint_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("endpoint_offset_seconds", sa.Numeric(12, 6)))
        batch.add_column(sa.Column("endpoint_tolerance_seconds", sa.Integer(), nullable=False,
                                   server_default="5"))
        batch.add_column(sa.Column("provenance_json", sa.JSON(), nullable=False, server_default="{}"))
        batch.drop_constraint("uq_live_outcome_observation_horizon", type_="unique")
        batch.create_unique_constraint("uq_live_outcome_observation_horizon_version",
                                       ["observation_id", "horizon", "evaluation_version"])
        for name in ("return_pct", "max_return_pct", "max_drawdown_pct"):
            batch.alter_column(name, existing_type=sa.Numeric(12, 6), type_=sa.Numeric())
    outcomes = sa.table("live_signal_outcomes", sa.column("next_evaluation_at"), sa.column("target_at"))
    op.execute(outcomes.update().values(next_evaluation_at=outcomes.c.target_at))
    with op.batch_alter_table("live_signal_outcomes") as batch:
        batch.alter_column("next_evaluation_at", existing_type=sa.DateTime(timezone=True), nullable=False)
    op.create_index("ix_live_outcomes_due", "live_signal_outcomes",
                    ["evaluation_version", "status", "next_evaluation_at", "id"])


def downgrade() -> None:
    if not context.is_offline_mode():
        outcomes = sa.table("live_signal_outcomes", sa.column("observation_id"), sa.column("horizon"),
                            sa.column("evaluation_version"), sa.column("return_pct"),
                            sa.column("max_return_pct"), sa.column("max_drawdown_pct"))
        duplicates = op.get_bind().execute(
            sa.select(outcomes.c.observation_id).group_by(
                outcomes.c.observation_id, outcomes.c.horizon,
            ).having(sa.func.count() > 1).limit(1)
        ).first()
        corrected = op.get_bind().execute(sa.select(outcomes.c.observation_id).where(
            sa.or_(outcomes.c.evaluation_version != "legacy-v1",
                   sa.func.abs(outcomes.c.return_pct) >= 1_000_000,
                   sa.func.abs(outcomes.c.max_return_pct) >= 1_000_000),
        ).limit(1)).first()
        if duplicates or corrected:
            raise RuntimeError("Forward versions coexist or corrected results exist; "
                               "refusing downgrade without explicit archival")
    op.drop_index("ix_live_outcomes_due", table_name="live_signal_outcomes")
    with op.batch_alter_table("live_signal_outcomes") as batch:
        batch.drop_constraint("uq_live_outcome_observation_horizon_version", type_="unique")
        batch.create_unique_constraint("uq_live_outcome_observation_horizon", ["observation_id", "horizon"])
        for name in ("return_pct", "max_return_pct", "max_drawdown_pct"):
            batch.alter_column(name, existing_type=sa.Numeric(), type_=sa.Numeric(12, 6))
        for name in ("provenance_json", "endpoint_tolerance_seconds", "endpoint_offset_seconds",
                     "endpoint_at", "next_evaluation_at", "evaluation_version"):
            batch.drop_column(name)
    op.drop_table("forward_evaluation_cursors")

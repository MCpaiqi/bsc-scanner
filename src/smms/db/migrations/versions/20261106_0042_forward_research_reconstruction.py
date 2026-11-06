"""Preserve Forward history, append research revisions and separate retry work."""

import sqlalchemy as sa
from alembic import context, op

revision = "20261106_0042"
down_revision = "20261106_0041"
branch_labels = None
depends_on = None


def upgrade():
    for table, constraint, identity in (
        ("live_signal_observations", "uq_live_observation_candidate_class", ["candidate_id", "signal_class"]),
        ("live_signal_outcomes", "uq_live_outcome_observation_horizon_version", ["observation_root_id", "horizon", "evaluation_version"]),
    ):
        with op.batch_alter_table(table) as batch:
            for column in (
                sa.Column("source_epoch", sa.Integer(), nullable=False, server_default="0"),
                sa.Column("research_revision", sa.Integer(), nullable=False, server_default="0"),
                sa.Column("snapshot_status", sa.String(24), nullable=False, server_default="INVALIDATED"),
                sa.Column("previous_snapshot_id", sa.Integer()),
                sa.Column("input_manifest", sa.JSON(), nullable=False, server_default="{}"),
            ):
                batch.add_column(column)
            if table == "live_signal_outcomes":
                batch.add_column(sa.Column("chain", sa.String(32), nullable=False, server_default="bsc"))
                batch.add_column(sa.Column("observation_root_id", sa.Integer()))
            batch.drop_constraint(constraint, type_="unique")
        if table == "live_signal_outcomes":
            op.execute("UPDATE live_signal_outcomes SET observation_root_id=observation_id")
            op.execute("UPDATE live_signal_outcomes SET chain=(SELECT chain FROM live_signal_observations "
                       "WHERE live_signal_observations.id=live_signal_outcomes.observation_id)")
        with op.batch_alter_table(table) as batch:
            if table == "live_signal_outcomes":
                batch.alter_column("observation_root_id", nullable=False)
                batch.create_foreign_key("fk_live_outcomes_root", "live_signal_observations", ["observation_root_id"], ["id"])
            batch.create_unique_constraint(constraint, [*identity, "research_revision"])
        op.create_index(f"ix_{'live_observations' if table == 'live_signal_observations' else 'live_outcomes'}_rebuild",
                        table, ["chain", "source_epoch", "snapshot_status", "id"])
    op.create_table("forward_evaluation_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("observation_id", sa.Integer(), sa.ForeignKey("live_signal_observations.id"), nullable=False),
        sa.Column("observation_root_id", sa.Integer(), sa.ForeignKey("live_signal_observations.id"), nullable=False),
        sa.Column("horizon", sa.String(8), nullable=False),
        sa.Column("target_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evaluation_version", sa.String(64), nullable=False),
        sa.Column("next_evaluation_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(64)),
        sa.Column("result_id", sa.Integer(), sa.ForeignKey("live_signal_outcomes.id")),
        sa.UniqueConstraint("observation_id", "horizon", "evaluation_version", name="uq_forward_job_identity"),
    )
    op.create_index("ix_forward_jobs_due", "forward_evaluation_jobs", ["status", "next_evaluation_at", "id"])


def downgrade():
    if not context.is_offline_mode():
        bind = op.get_bind()
        if bind.execute(sa.text("SELECT 1 FROM forward_evaluation_jobs LIMIT 1")).first():
            raise RuntimeError("Forward reconstruction work exists; refusing lossy downgrade")
        for table in ("live_signal_observations", "live_signal_outcomes"):
            if bind.execute(sa.text(f"SELECT 1 FROM {table} WHERE research_revision>0 OR source_epoch>0 "
                                    "OR CAST(input_manifest AS VARCHAR) != '{}' LIMIT 1")).first():
                raise RuntimeError("Forward research revisions exist; refusing lossy downgrade")
        if bind.execute(sa.text("SELECT 1 FROM research_rebuild_jobs WHERE kind IN "
                                "('live_signal_observations','live_signal_outcomes') LIMIT 1")).first():
            raise RuntimeError("Forward reconstruction work exists; refusing lossy downgrade")
    op.drop_table("forward_evaluation_jobs")
    for table, constraint, identity in (
        ("live_signal_outcomes", "uq_live_outcome_observation_horizon_version", ["observation_id", "horizon", "evaluation_version"]),
        ("live_signal_observations", "uq_live_observation_candidate_class", ["candidate_id", "signal_class"]),
    ):
        op.drop_index(f"ix_{'live_observations' if table == 'live_signal_observations' else 'live_outcomes'}_rebuild", table_name=table)
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(constraint, type_="unique")
            batch.create_unique_constraint(constraint, identity)
            if table == "live_signal_outcomes":
                batch.drop_constraint("fk_live_outcomes_root", type_="foreignkey")
                batch.drop_column("observation_root_id")
                batch.drop_column("chain")
            for name in ("input_manifest", "previous_snapshot_id", "snapshot_status", "research_revision", "source_epoch"):
                batch.drop_column(name)

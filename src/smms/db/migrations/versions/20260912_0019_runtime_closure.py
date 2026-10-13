"""Phase 7.9.4 runtime integration closure.

Pre-7.9.4 live observations came from the BSC-only production runtime, so the
``bsc`` backfill is deterministic rather than a guess about unknown chain data.
"""

import sqlalchemy as sa
from alembic import op

revision = "20260912_0019"
down_revision = "20260912_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("live_signal_observations") as batch:
        batch.add_column(sa.Column("chain", sa.String(32), nullable=True))
    op.execute("UPDATE live_signal_observations SET chain = 'bsc' WHERE chain IS NULL")
    with op.batch_alter_table("live_signal_observations") as batch:
        batch.alter_column("chain", existing_type=sa.String(32), nullable=False)
        batch.drop_index("ix_live_observations_token_time")
        batch.create_index(
            "ix_live_observations_token_time", ["chain", "token_address", "observed_at"]
        )

    op.create_table(
        "evidence_observations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chain", sa.String(32), nullable=False),
        sa.Column("subject_type", sa.String(16), nullable=False),
        sa.Column("subject_address", sa.String(128), nullable=False),
        sa.Column("field", sa.String(64), nullable=False),
        sa.Column("value_json", sa.JSON()),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4)),
        sa.Column("evidence_level", sa.String(32), nullable=False),
        sa.Column("raw_reference_json", sa.JSON()),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("stale", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "chain", "subject_address", "field", "source", "fetched_at",
            name="uq_evidence_observation_identity",
        ),
    )
    op.create_index(
        "ix_evidence_observation_lookup",
        "evidence_observations",
        ["chain", "subject_address", "field", "observed_at"],
    )
    op.create_table(
        "worker_heartbeats",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("instance_id", sa.String(128), nullable=False),
        sa.Column("worker_type", sa.String(32), nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("current_job", sa.String(128)),
        sa.Column("details_json", sa.JSON(), nullable=False),
        sa.UniqueConstraint(
            "instance_id", "worker_type", name="uq_worker_heartbeat_instance"
        ),
    )
    op.create_index(
        "ix_worker_heartbeat_type_time",
        "worker_heartbeats",
        ["worker_type", "last_heartbeat_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_worker_heartbeat_type_time", table_name="worker_heartbeats")
    op.drop_table("worker_heartbeats")
    op.drop_index("ix_evidence_observation_lookup", table_name="evidence_observations")
    op.drop_table("evidence_observations")
    with op.batch_alter_table("live_signal_observations") as batch:
        batch.drop_index("ix_live_observations_token_time")
        batch.create_index(
            "ix_live_observations_token_time", ["token_address", "observed_at"]
        )
        batch.drop_column("chain")

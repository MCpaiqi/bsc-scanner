"""Canonical radar candidates, evidence references and status history."""

import sqlalchemy as sa
from alembic import op

revision = "20260912_0015"
down_revision = "20260912_0014"
branch_labels = None
depends_on = None

STATUSES = (
    "DISCOVERED",
    "PREQUALIFIED",
    "WATCH",
    "INTERESTING",
    "HIGH_SIGNAL",
    "HIGH_RISK",
    "REJECTED",
    "STALE",
)


def status_type(name="candidate_status"):
    return sa.Enum(*STATUSES, native_enum=False, create_constraint=True, name=name, length=20)


def ref(name, table, ondelete="SET NULL", nullable=True):
    return sa.Column(
        name, sa.Integer(), sa.ForeignKey(f"{table}.id", ondelete=ondelete), nullable=nullable
    )


def upgrade():
    op.create_table(
        "radar_candidates",
        sa.Column("id", sa.Integer(), primary_key=True),
        ref("token_id", "tokens", "CASCADE", False),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("discovery_source", sa.String(32), nullable=False),
        sa.Column("discovery_reason", sa.String(128), nullable=False),
        sa.Column("first_seen_block", sa.Integer()),
        sa.Column("latest_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", status_type(), nullable=False),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("priority_score", sa.Numeric(6, 2), nullable=False),
        sa.Column("features_json", sa.JSON(), nullable=False),
        sa.Column("priority_json", sa.JSON(), nullable=False),
        ref("latest_signal_id", "signals"),
        ref("smart_snapshot_id", "token_smart_money_snapshots"),
        ref("risk_snapshot_id", "token_risk_snapshots"),
        sa.Column("data_status", sa.String(32), nullable=False),
        sa.Column("invalidated_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.UniqueConstraint("token_id"),
    )
    op.create_index("ix_radar_status_priority", "radar_candidates", ["status", "priority_score"])
    op.create_table(
        "radar_candidate_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        ref("candidate_id", "radar_candidates", "CASCADE", False),
        sa.Column("previous_status", status_type("candidate_previous_status")),
        sa.Column("new_status", status_type(), nullable=False),
        sa.Column("reason", sa.String(128), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_radar_candidate_history_candidate_id", "radar_candidate_history", ["candidate_id"]
    )
    op.create_table(
        "radar_candidate_evidence",
        sa.Column("id", sa.Integer(), primary_key=True),
        ref("candidate_id", "radar_candidates", "CASCADE", False),
        sa.Column("source_key", sa.String(64), nullable=False),
        sa.Column("source_kind", sa.String(32), nullable=False),
        ref("pair_id", "pairs"),
        ref("lifecycle_id", "token_lifecycle_events"),
        ref("trade_id", "trade_events"),
        ref("market_id", "market_events"),
        ref("raw_event_id", "raw_chain_events"),
        sa.Column("raw_required", sa.Boolean(), nullable=False),
        sa.Column("block_hash", sa.String(128)),
        sa.Column("valid", sa.Boolean(), nullable=False),
    )
    op.create_index(
        "uq_radar_evidence_source",
        "radar_candidate_evidence",
        ["candidate_id", "source_key"],
        unique=True,
    )
    op.create_index(
        "ix_radar_candidate_evidence_candidate_id", "radar_candidate_evidence", ["candidate_id"]
    )
    op.create_index(
        "ix_radar_candidate_evidence_raw_event_id", "radar_candidate_evidence", ["raw_event_id"]
    )


def downgrade():
    op.drop_table("radar_candidate_evidence")
    op.drop_table("radar_candidate_history")
    op.drop_table("radar_candidates")

"""Phase 7.9.2 analyst annotations, alert rules and delivery history."""

import sqlalchemy as sa
from alembic import op

revision = "20260912_0018"
down_revision = "20260912_0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "radar_annotations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chain", sa.String(32), nullable=False),
        sa.Column("token_address", sa.String(128), nullable=False),
        sa.Column("favorite", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("note", sa.Text()),
        sa.Column("tags", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("chain", "token_address", name="uq_radar_annotation_token"),
    )
    op.create_index("ix_radar_annotations_chain", "radar_annotations", ["chain"])
    op.create_index("ix_radar_annotations_token_address", "radar_annotations", ["token_address"])
    op.create_table(
        "alert_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("chain", sa.String(32)),
        sa.Column("conditions_json", sa.JSON(), nullable=False),
        sa.Column("match_mode", sa.String(8), nullable=False, server_default="all"),
        sa.Column("notification_channels", sa.JSON(), nullable=False),
        sa.Column("cooldown_seconds", sa.Integer(), nullable=False, server_default="300"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_alert_rules_chain", "alert_rules", ["chain"])
    op.create_index("ix_alert_rules_enabled", "alert_rules", ["enabled", "chain"])
    op.create_table(
        "alert_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "rule_id",
            sa.Integer(),
            sa.ForeignKey("alert_rules.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chain", sa.String(32), nullable=False),
        sa.Column("token_address", sa.String(128), nullable=False),
        sa.Column("state_key", sa.String(64), nullable=False),
        sa.Column("state_json", sa.JSON(), nullable=False),
        sa.Column("matched", sa.Boolean(), nullable=False),
        sa.Column("suppressed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reason", sa.String(32)),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("notified_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_alert_history_lookup",
        "alert_history",
        ["chain", "token_address", "rule_id", "state_key"],
    )
    op.create_table(
        "alert_notifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "history_id",
            sa.Integer(),
            sa.ForeignKey("alert_history.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="DELIVERED"),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_alert_notifications_history", "alert_notifications", ["history_id", "channel"]
    )


def downgrade() -> None:
    op.drop_index("ix_alert_notifications_history", table_name="alert_notifications")
    op.drop_table("alert_notifications")
    op.drop_index("ix_alert_history_lookup", table_name="alert_history")
    op.drop_table("alert_history")
    op.drop_index("ix_alert_rules_enabled", table_name="alert_rules")
    op.drop_index("ix_alert_rules_chain", table_name="alert_rules")
    op.drop_table("alert_rules")
    op.drop_index("ix_radar_annotations_token_address", table_name="radar_annotations")
    op.drop_index("ix_radar_annotations_chain", table_name="radar_annotations")
    op.drop_table("radar_annotations")

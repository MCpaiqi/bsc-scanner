"""Explicit analyst rule subjects and retry-safe manual writes."""

import sqlalchemy as sa
from alembic import context, op

revision = "20261107_0045"
down_revision = "20261107_0044"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("radar_annotations") as batch:
        batch.add_column(sa.Column("revision", sa.Integer(), server_default="1", nullable=False))
        batch.add_column(sa.Column("deleted", sa.Boolean(), server_default=sa.false(), nullable=False))
        batch.create_check_constraint("ck_annotation_revision", "revision >= 1")
    with op.batch_alter_table("alert_rules") as batch:
        batch.add_column(sa.Column("token_address", sa.String(128)))
        batch.add_column(sa.Column("request_id", sa.String(36)))
        batch.add_column(sa.Column("deleted", sa.Boolean(), server_default=sa.false(), nullable=False))
        batch.create_unique_constraint("uq_alert_rule_request_id", ["request_id"])
        batch.create_check_constraint("ck_alert_rule_target_chain",
                                      "token_address IS NULL OR (chain IS NOT NULL AND chain = 'bsc')")


def downgrade():
    if context.is_offline_mode():
        raise RuntimeError("analyst downgrade requires an online scope/revision check")
    lossy = op.get_bind().scalar(sa.text("SELECT EXISTS(SELECT 1 FROM alert_rules WHERE token_address IS NOT NULL "
        "OR request_id IS NOT NULL OR deleted) OR EXISTS(SELECT 1 FROM radar_annotations WHERE revision <> 1 OR deleted)"))
    if lossy:
        raise RuntimeError("cannot discard analyst scope/revision metadata")
    with op.batch_alter_table("alert_rules") as batch:
        batch.drop_constraint("ck_alert_rule_target_chain", type_="check")
        batch.drop_constraint("uq_alert_rule_request_id", type_="unique")
        batch.drop_column("deleted")
        batch.drop_column("request_id")
        batch.drop_column("token_address")
    with op.batch_alter_table("radar_annotations") as batch:
        batch.drop_constraint("ck_annotation_revision", type_="check")
        batch.drop_column("deleted")
        batch.drop_column("revision")

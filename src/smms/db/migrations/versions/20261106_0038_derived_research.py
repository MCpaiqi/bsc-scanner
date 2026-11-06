"""Quarantine stale derived research and preserve append-only reconstruction."""

import sqlalchemy as sa
from alembic import context, op

revision = "20261106_0038"
down_revision = "20261106_0037"
branch_labels = None
depends_on = None

IDENTITIES = {
    "wallet_smart_money_snapshots": ("uq_wallet_smart_money_snapshot_version", [
        "chain", "wallet_address", "as_of", "score_version", "profile_version"]),
    "token_smart_money_snapshots": ("uq_token_smart_money_snapshot_version", [
        "chain", "token_address", "as_of", "score_version"]),
    "token_risk_snapshots": ("uq_token_risk_snapshot_version", [
        "chain", "token_address", "as_of", "risk_version"]),
    "token_market_snapshots": ("uq_token_market_snapshot_identity", [
        "chain", "token_address", "pair_address", "source", "as_of"]),
}


def upgrade():
    op.create_table("research_states", sa.Column("chain", sa.String(32), primary_key=True),
                             sa.Column("epoch", sa.Integer(), nullable=False))
    op.create_table("research_epochs", sa.Column("chain", sa.String(32), primary_key=True),
                             sa.Column("epoch", sa.Integer(), primary_key=True),
                             sa.Column("reason", sa.String(64), nullable=False),
                             sa.Column("cause_json", sa.JSON(), nullable=False),
                             sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.execute(sa.text("INSERT INTO research_states (chain,epoch) VALUES ('bsc',1)"))
    op.execute(sa.text("INSERT INTO research_epochs (chain,epoch,reason,cause_json,created_at) VALUES "
                       "('bsc',1,'MIGRATION_QUARANTINE','{\"migration\": \"20261106_0038\"}',CURRENT_TIMESTAMP)"))
    op.create_table("research_snapshot_locks", sa.Column("identity", sa.String(64), primary_key=True))
    op.create_table("research_rebuild_cursors", sa.Column("kind", sa.String(64), primary_key=True),
                    sa.Column("last_id", sa.Integer(), nullable=False))
    op.create_table("research_rebuild_jobs", sa.Column("id", sa.Integer(), primary_key=True),
                    sa.Column("kind", sa.String(64), nullable=False),
                    sa.Column("snapshot_id", sa.Integer(), nullable=False),
                    sa.Column("requested_epoch", sa.Integer(), nullable=False),
                    sa.Column("status", sa.String(24), nullable=False),
                    sa.Column("attempts", sa.Integer(), nullable=False),
                    sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
                    sa.Column("result_id", sa.Integer()), sa.Column("last_error", sa.String(64)),
                    sa.UniqueConstraint("kind", "snapshot_id", "requested_epoch", name="uq_research_rebuild_identity"))
    op.create_index("ix_research_rebuild_due", "research_rebuild_jobs", ["status", "next_attempt_at", "id"])
    for table, (constraint, keys) in IDENTITIES.items():
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("source_epoch", sa.Integer(), nullable=False, server_default="0"))
            batch.add_column(sa.Column("research_revision", sa.Integer(), nullable=False, server_default="0"))
            batch.add_column(sa.Column("snapshot_status", sa.String(24), nullable=False, server_default="VALID"))
            batch.add_column(sa.Column("previous_snapshot_id", sa.Integer()))
            batch.add_column(sa.Column("input_manifest", sa.JSON(), nullable=False, server_default="{}"))
            batch.drop_constraint(constraint, type_="unique")
            batch.create_unique_constraint(constraint, [*keys, "research_revision"])
    # External provider observations are primary evidence, independent of chain rollups.
    op.execute(sa.text("UPDATE token_market_snapshots SET source_epoch=-1 WHERE source='DEXSCREENER'"))


def downgrade():
    if not context.is_offline_mode():
        bind = op.get_bind()
        used = bind.execute(sa.text("SELECT 1 FROM research_epochs WHERE epoch > 1 LIMIT 1")).first()
        used = used or bind.execute(sa.text("SELECT 1 FROM research_rebuild_jobs LIMIT 1")).first()
        for table in IDENTITIES:
            used = used or bind.execute(sa.text(f"SELECT 1 FROM {table} WHERE research_revision > 0 "
                                                "OR source_epoch > 0 OR CAST(input_manifest AS VARCHAR) != '{}' LIMIT 1")).first()
        if used:
            raise RuntimeError("Derived research revisions exist; refusing downgrade without explicit archival")
    for table, (constraint, keys) in IDENTITIES.items():
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(constraint, type_="unique")
            batch.create_unique_constraint(constraint, keys)
            for name in ("input_manifest", "previous_snapshot_id", "snapshot_status", "research_revision", "source_epoch"):
                batch.drop_column(name)
    for table in ("research_rebuild_jobs", "research_rebuild_cursors", "research_snapshot_locks",
                  "research_epochs", "research_states"):
        op.drop_table(table)

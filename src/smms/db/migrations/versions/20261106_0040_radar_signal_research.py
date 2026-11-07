"""Preserve candidate/Signal history and quarantine pre-lineage projections."""

import sqlalchemy as sa
from alembic import context, op

revision = "20261106_0040"
down_revision = "20261106_0039"
branch_labels = None
depends_on = None


def lineage_columns():
    return [sa.Column("source_epoch", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("research_revision", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("snapshot_status", sa.String(24), nullable=False, server_default="VALID"),
            sa.Column("previous_snapshot_id", sa.Integer()),
            sa.Column("input_manifest", sa.JSON(), nullable=False, server_default="{}")]


def upgrade():
    op.create_table("research_snapshot_dependencies",
                    sa.Column("parent_kind", sa.String(64), primary_key=True),
                    sa.Column("parent_id", sa.Integer(), primary_key=True),
                    sa.Column("source_kind", sa.String(64), primary_key=True),
                    sa.Column("source_id", sa.Integer(), primary_key=True),
                    sa.Column("source_digest", sa.String(64)))
    op.create_index("ix_research_dependency_source", "research_snapshot_dependencies", ["source_kind", "source_id"])
    op.create_table("radar_feature_observations", sa.Column("id", sa.Integer(), primary_key=True),
                    sa.Column("chain", sa.String(32), nullable=False), sa.Column("token_address", sa.String(128), nullable=False),
                    sa.Column("candidate_id", sa.Integer(), sa.ForeignKey("radar_candidates.id", ondelete="CASCADE"), nullable=False),
                    sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
                    sa.Column("config_hash", sa.String(64), nullable=False),
                    sa.Column("features_json", sa.JSON(), nullable=False), sa.Column("priority_json", sa.JSON(), nullable=False),
                    sa.Column("context_json", sa.JSON(), nullable=False),
                    sa.Column("smart_snapshot_id", sa.Integer(), sa.ForeignKey("token_smart_money_snapshots.id", ondelete="SET NULL")),
                    sa.Column("risk_snapshot_id", sa.Integer(), sa.ForeignKey("token_risk_snapshots.id", ondelete="SET NULL")),
                    sa.Column("data_status", sa.String(32), nullable=False), *lineage_columns(),
                    sa.UniqueConstraint("candidate_id", "as_of", "config_hash", "research_revision", name="uq_radar_feature_revision"))
    op.create_index("ix_radar_feature_rebuild", "radar_feature_observations", ["chain", "source_epoch", "snapshot_status", "id"])
    with op.batch_alter_table("radar_candidates") as batch:
        batch.add_column(sa.Column("projection_id", sa.Integer()))
        batch.create_foreign_key("fk_radar_candidate_projection", "radar_feature_observations", ["projection_id"], ["id"], ondelete="SET NULL")
        batch.create_index("ix_radar_candidates_projection_id", ["projection_id"])
    json_object = "json_build_object" if op.get_bind().dialect.name == "postgresql" else "json_object"
    legacy_context = (f"{json_object}('stage',c.stage,'discovered_at',c.discovered_at,'discovery_source',c.discovery_source,"
                      "'first_seen_block',c.first_seen_block,'latest_seen_at',c.latest_seen_at,'status',c.status,"
                      "'priority_score',c.priority_score,'latest_signal_id',c.latest_signal_id,'discovery_reason',c.discovery_reason)")
    op.execute(sa.text("INSERT INTO radar_feature_observations "
                       "(chain,token_address,candidate_id,as_of,config_hash,features_json,priority_json,context_json,"
                       "smart_snapshot_id,risk_snapshot_id,data_status) SELECT t.chain,t.address,c.id,c.updated_at,"
                       f"'legacy',c.features_json,c.priority_json,{legacy_context},c.smart_snapshot_id,c.risk_snapshot_id,c.data_status "
                       "FROM radar_candidates c JOIN tokens t ON t.id=c.token_id"))
    op.execute(sa.text("UPDATE radar_candidates SET projection_id=(SELECT id FROM radar_feature_observations r WHERE r.candidate_id=radar_candidates.id)"))
    with op.batch_alter_table("signals") as batch:
        batch.add_column(sa.Column("chain", sa.String(32), nullable=False, server_default="bsc"))
        for column in lineage_columns():
            batch.add_column(column)
        batch.drop_constraint("uq_signals_token_time_version", type_="unique")
        batch.create_unique_constraint("uq_signals_token_time_version", ["token_id", "created_at", "model_version", "research_revision"])
        batch.create_index("ix_signals_rebuild", ["chain", "source_epoch", "snapshot_status", "id"])
    op.execute(sa.text("UPDATE signals SET chain=(SELECT chain FROM tokens WHERE tokens.id=signals.token_id)"))
    for table in ("token_market_snapshots", "token_risk_snapshots", "wallet_smart_money_snapshots",
                  "token_smart_money_snapshots", "early_buyers", "wallet_token_positions"):
        if op.get_bind().dialect.name == "postgresql":
            source = (f"{table} t CROSS JOIN LATERAL json_array_elements(CASE WHEN "
                      "json_typeof(t.input_manifest->'dependencies')='array' THEN t.input_manifest->'dependencies' "
                      "ELSE '[]'::json END) d")
            values = "d->>'kind',CAST(d->>'id' AS INTEGER),d->>'digest'"
            condition = "d->>'id' ~ '^[0-9]{1,10}$' AND CAST(d->>'id' AS NUMERIC) <= 2147483647 AND d->>'kind' IS NOT NULL"
        else:
            source = (f"{table} t,json_each(CASE WHEN json_type(t.input_manifest,'$.dependencies')='array' "
                      "THEN json_extract(t.input_manifest,'$.dependencies') ELSE '[]' END) d")
            values = "json_extract(d.value,'$.kind'),CAST(json_extract(d.value,'$.id') AS INTEGER),json_extract(d.value,'$.digest')"
            condition = "json_type(d.value,'$.id')='integer' AND json_extract(d.value,'$.kind') IS NOT NULL"
        op.execute(sa.text("INSERT INTO research_snapshot_dependencies (parent_kind,parent_id,source_kind,source_id,source_digest) "
                           f"SELECT DISTINCT '{table}',t.id,{values} FROM {source} WHERE {condition}"))
        # The older visibility contract did not verify same-epoch dependencies.
        # Recompute those outputs once; do not certify them merely by backfilling edges.
        op.execute(sa.text(f"UPDATE {table} SET snapshot_status='INVALIDATED' WHERE snapshot_status='VALID' AND id IN "
                           f"(SELECT parent_id FROM research_snapshot_dependencies WHERE parent_kind='{table}')"))


def downgrade():
    if not context.is_offline_mode():
        bind = op.get_bind()
        if bind.execute(sa.text("SELECT 1 FROM research_snapshot_dependencies LIMIT 1")).first():
            raise RuntimeError("Research dependencies exist; refusing lossy downgrade")
        if bind.execute(sa.text("SELECT 1 FROM research_rebuild_jobs WHERE kind IN ('signals','radar_feature_observations') LIMIT 1")).first():
            raise RuntimeError("Radar reconstruction work exists; refusing lossy downgrade")
        for table in ("signals", "radar_feature_observations"):
            if bind.execute(sa.text(f"SELECT 1 FROM {table} WHERE source_epoch > 0 OR research_revision > 0 "
                                    "OR CAST(input_manifest AS VARCHAR) != '{}' LIMIT 1")).first():
                raise RuntimeError("Radar/Signal revisions exist; refusing lossy downgrade")
    with op.batch_alter_table("signals") as batch:
        batch.drop_index("ix_signals_rebuild")
        batch.drop_constraint("uq_signals_token_time_version", type_="unique")
        batch.create_unique_constraint("uq_signals_token_time_version", ["token_id", "created_at", "model_version"])
        for name in ("chain", "source_epoch", "research_revision", "snapshot_status", "previous_snapshot_id", "input_manifest"):
            batch.drop_column(name)
    with op.batch_alter_table("radar_candidates") as batch:
        batch.drop_index("ix_radar_candidates_projection_id")
        batch.drop_constraint("fk_radar_candidate_projection", type_="foreignkey")
        batch.drop_column("projection_id")
    op.drop_table("radar_feature_observations")
    op.drop_table("research_snapshot_dependencies")

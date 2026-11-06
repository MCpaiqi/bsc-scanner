"""Quarantine unsafe wallet input recipes; preserve unknown flows as SQL NULL."""

import sqlalchemy as sa
from alembic import context, op

revision = "20261106_0043"
down_revision = "20261106_0042"
branch_labels = None
depends_on = None

FIELDS = ("smart_wallet_count", "smart_money_buy_usd", "smart_money_sell_usd",
          "smart_money_net_flow", "early_smart_buy_count")
TABLES = ("wallet_smart_money_snapshots", "token_smart_money_snapshots", "token_market_snapshots",
          "token_risk_snapshots", "early_buyers", "wallet_token_positions", "radar_feature_observations",
          "signals", "backtest_datasets", "live_signal_observations", "live_signal_outcomes")
SOURCES = ("wallet_smart_money_snapshots", "token_smart_money_snapshots")


def upgrade():
    with op.batch_alter_table("token_smart_money_snapshots") as batch:
        for name in FIELDS:
            batch.alter_column(name, existing_type=sa.Integer() if name.endswith("count") else sa.Numeric(38, 18), nullable=True)
    # Input normalization changed, not chain canonicality or score-v2 weights.
    # Quarantine precisely these recipes and their existing transitive consumers.
    # UNION deduplicates cycles; history and original payloads remain untouched.
    roots = " UNION ".join(f"SELECT '{table}' AS kind,id FROM {table} WHERE chain='bsc' "
                           "AND snapshot_status IN ('VALID','UNAVAILABLE','INVALIDATED')" for table in SOURCES)
    affected = ("WITH RECURSIVE wallet_policy_affected(kind,id) AS (" + roots
                + " UNION SELECT d.parent_kind,d.parent_id FROM research_snapshot_dependencies d "
                  "JOIN wallet_policy_affected a ON d.source_kind=a.kind AND d.source_id=a.id) ")
    for table in TABLES:
        op.execute(sa.text(affected + f"UPDATE {table} SET snapshot_status='INVALIDATED' "
                          "WHERE snapshot_status IN ('VALID','UNAVAILABLE') "
                          f"AND id IN (SELECT id FROM wallet_policy_affected WHERE kind='{table}')"))


def downgrade():
    if not context.is_offline_mode():
        bind = op.get_bind()
        if bind.execute(sa.text("SELECT 1 FROM token_smart_money_snapshots WHERE " + " OR ".join(f"{name} IS NULL" for name in FIELDS) + " LIMIT 1")).first():
            raise RuntimeError("Unknown smart-money flows exist; refusing lossy downgrade")
        for table in SOURCES:
            if bind.execute(sa.text(f"SELECT 1 FROM {table} WHERE CAST(input_manifest AS VARCHAR) LIKE '%usd-v2%' LIMIT 1")).first():
                raise RuntimeError("USD-v2 research exists; refusing lossy downgrade")
        if bind.execute(sa.text("SELECT 1 FROM research_rebuild_jobs WHERE kind IN "
                                "('wallet_smart_money_snapshots','token_smart_money_snapshots') LIMIT 1")).first():
            raise RuntimeError("Wallet reconstruction work exists; refusing lossy downgrade")
    with op.batch_alter_table("token_smart_money_snapshots") as batch:
        for name in FIELDS:
            batch.alter_column(name, existing_type=sa.Integer() if name.endswith("count") else sa.Numeric(38, 18), nullable=False)

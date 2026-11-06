"""Upgrade real pre-lineage values, quarantine them and rebuild canonical history."""

import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import MetaData, Table, create_engine, select
from sqlalchemy.orm import Session

from smms.db.models import (
    Pair,
    Token,
    TokenMarketSnapshot,
    TokenRiskSnapshot,
    TokenSmartMoneySnapshot,
    TradeEvent,
)
from smms.research.rebuild import rebuild_due

URL = os.getenv("SMMS_MIGRATION_TEST_DATABASE_URL")
ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(not URL, reason="explicit isolated PostgreSQL migration URL required")
def test_pre_lineage_snapshots_are_preserved_quarantined_and_reconstructed():
    owner = create_engine(URL)
    assert owner.dialect.name == "postgresql"
    schema = "smms_derived_" + uuid.uuid4().hex
    with owner.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    engine = create_engine(URL, connect_args={"options": f"-csearch_path={schema}"})
    env = dict(os.environ, DATABASE_URL=URL, PGOPTIONS=f"-csearch_path={schema}", ENABLE_LIVE_TRADING="false")

    def alembic(*arguments):
        return subprocess.run([sys.executable, "-m", "alembic", *arguments], cwd=ROOT, env=env,
                              capture_output=True, text=True, timeout=60, check=False)

    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    cutoff = t0 + timedelta(seconds=20)
    try:
        old = alembic("upgrade", "20261106_0037")
        assert old.returncode == 0, old.stderr
        with Session(engine) as session:
            session.add_all([Token(chain="bsc", address="token"), Token(chain="bsc", address="quote"),
                             Pair(chain_id=56, address="pair", token0_address="token", token1_address="quote",
                                  quote_token="quote", creation_timestamp=t0, creation_block=100),
                             TradeEvent(chain="bsc", token_address="token", pair_address="pair", trader_address="wallet",
                                        tx_hash="fact", timestamp=t0 + timedelta(seconds=10), side="BUY",
                                        token_amount=1, quote_amount=10, value_usd=10, price=1, source="fixture")])
            session.commit()
        with engine.begin() as connection:
            meta = MetaData()
            smart = Table("token_smart_money_snapshots", meta, autoload_with=connection)
            connection.execute(smart.insert().values(
                chain="bsc", token_address="token", as_of=cutoff, score_version="v2", smart_wallet_count=7,
                smart_money_buy_usd=999, smart_money_sell_usd=0, smart_money_net_flow=999, early_smart_buy_count=7,
                smart_money_score=99, confidence="HIGH", confidence_score=99, data_status="OK", top_smart_wallets_json=[],
                updated_at=cutoff,
            ))
            risk = Table("token_risk_snapshots", meta, autoload_with=connection)
            connection.execute(risk.insert().values(
                chain="bsc", token_address="token", as_of=cutoff, risk_version="risk-v1",
                **dict.fromkeys(("contract_risk", "liquidity_risk", "holder_risk", "behavior_risk", "overall_risk"), "CRITICAL"),
                confidence="HIGH", evidence_json=["legacy"], dimensions_json={}, updated_at=cutoff,
            ))
            market = Table("token_market_snapshots", meta, autoload_with=connection)
            for source, price in (("ON_CHAIN_TRADE_AGGREGATION", 77), ("DEXSCREENER", 2)):
                connection.execute(market.insert().values(
                    chain="bsc", chain_id=56, token_address="token", pair_address="pair", source=source,
                    observed_at=cutoff, as_of=cutoff, volume_5m=999, price_usd=price, freshness_seconds=0,
                    status="KNOWN", metadata_json={}, created_at=cutoff,
                ))
        upgraded = alembic("upgrade", "head")
        assert upgraded.returncode == 0, upgraded.stderr
        with Session(engine) as session:
            assert session.scalar(select(TokenSmartMoneySnapshot)) is None
            assert session.scalar(select(TokenRiskSnapshot)) is None
            external = session.scalar(select(TokenMarketSnapshot))
            assert external.source == "DEXSCREENER" and external.source_epoch == -1 and external.price_usd == 2
            for _ in range(3):
                rebuild_due(session, now=t0 + timedelta(days=2), batch_size=16)
                session.commit()
            current = session.scalar(select(TokenSmartMoneySnapshot))
            assert current.research_revision == 1 and current.source_epoch == 1 and current.smart_money_net_flow != 999
            assert current.input_manifest["parameters"]["reconstruction_policy"] == "legacy-defaults-v1"
            old_smart = session.scalar(select(TokenSmartMoneySnapshot).where(TokenSmartMoneySnapshot.research_revision == 0)
                                       .execution_options(include_invalid_snapshots=True))
            assert old_smart.smart_money_net_flow == 999
            corrected_risk = session.scalar(select(TokenRiskSnapshot))
            assert corrected_risk.contract_risk == corrected_risk.liquidity_risk == "UNKNOWN"
            assert corrected_risk.overall_risk != "CRITICAL"
            rebuilt_market = session.scalar(select(TokenMarketSnapshot).where(TokenMarketSnapshot.source == "ON_CHAIN_TRADE_AGGREGATION"))
            assert rebuilt_market.volume_5m == 10 and rebuilt_market.price_usd is None
        refused = alembic("downgrade", "20261106_0037")
        assert refused.returncode != 0 and "refusing" in refused.stderr
        assert "20261106_0040" in alembic("current").stdout
    finally:
        engine.dispose()
        try:
            with owner.begin() as connection:
                connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        finally:
            owner.dispose()

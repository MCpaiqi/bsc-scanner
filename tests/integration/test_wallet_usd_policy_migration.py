"""Upgrade actual unsafe v1 inputs and rebuild transitive research history."""

import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from smms.backtest.dataset import HistoricalDatasetBuilder, load_materialized_dataset
from smms.db.models import (
    BacktestDataset,
    LiveSignalObservation,
    LiveSignalOutcome,
    Pair,
    Signal,
    Token,
    TokenRiskSnapshot,
    TokenSmartMoneySnapshot,
    TradeEvent,
    WalletSmartMoneySnapshot,
)
from smms.radar.engine import RadarEngine
from smms.radar.live_forward import evaluate_due, record_observation
from smms.radar.live_signal import LiveSignalService
from smms.research.lineage import manifest
from smms.research.publication import publish_snapshot
from smms.research.rebuild import rebuild_due
from smms.smart_money.aggregation import IntelligenceAggregationService

URL = os.getenv("SMMS_MIGRATION_TEST_DATABASE_URL")
ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(not URL, reason="explicit isolated PostgreSQL migration URL required")
def test_usd_policy_preserves_values_quarantines_dependents_and_recovers_after_restart():
    owner = create_engine(URL)
    schema = "smms_wallet_policy_" + uuid.uuid4().hex
    assert owner.dialect.name == "postgresql"
    with owner.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    engine = create_engine(URL, connect_args={"options": f"-csearch_path={schema}"})
    env = dict(os.environ, DATABASE_URL=URL, PGOPTIONS=f"-csearch_path={schema}", ENABLE_LIVE_TRADING="false")

    def alembic(*args):
        return subprocess.run([sys.executable, "-m", "alembic", *args], cwd=ROOT, env=env,
                              capture_output=True, text=True, timeout=60, check=False)

    t0, cutoff = datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, 0, 0, 20, tzinfo=UTC)
    try:
        result = alembic("upgrade", "20261106_0042")
        assert result.returncode == 0, result.stderr
        with Session(engine) as session:
            pair = Pair(chain_id=56, address="pair", token0_address="token", token1_address="quote", creation_timestamp=t0)
            session.add_all([Token(chain="bsc", address="token"), pair,
                TradeEvent(chain="bsc", token_address="token", pair_address="pair", trader_address="wallet", tx_hash="entry",
                           timestamp=t0 + timedelta(seconds=10), side="BUY", token_amount=1, value_usd=10, price=999, price_usd=10, source="fixture"),
                TradeEvent(chain="bsc", token_address="token", pair_address="pair", trader_address="other", tx_hash="endpoint",
                           timestamp=cutoff + timedelta(seconds=30), side="BUY", token_amount=1, value_usd=20, price=999, price_usd=20, source="fixture")])
            session.flush()
            wallet = publish_snapshot(session, WalletSmartMoneySnapshot(chain="bsc", wallet_address="wallet", as_of=cutoff,
                score_version="v2", profile_version="v1", source_epoch=1, score=99, confidence="HIGH", confidence_score=99,
                sample_size=1, data_completeness=1, historical_coverage=1, trade_quality=1,
                profile_json={"roi": "999", "total_invested": "10"}, components_json={}, contributions_json={},
                input_manifest=manifest(kind="wallet_smart_money_snapshots", parameters={"input_policy": "canonical-wallet-replay-v1"}, facts={})))
            token = publish_snapshot(session, TokenSmartMoneySnapshot(chain="bsc", token_address="token", as_of=cutoff,
                score_version="v2", source_epoch=1, smart_wallet_count=1, smart_money_buy_usd=999, smart_money_sell_usd=0,
                smart_money_net_flow=999, early_smart_buy_count=1, smart_money_score=99, confidence="HIGH", confidence_score=99, data_status="OK",
                input_manifest=manifest(kind="token_smart_money_snapshots", parameters={"input_policy": "canonical-research-v1", "min_wallet_score": "0"},
                    facts={}, dependencies=[{"kind": wallet.__tablename__, "id": wallet.id, "digest": wallet.input_manifest["digest"]}])))
            risk = publish_snapshot(session, TokenRiskSnapshot(chain="bsc", token_address="token", as_of=cutoff,
                risk_version="risk-v1", source_epoch=1, contract_risk="LOW", liquidity_risk="LOW", holder_risk="LOW", behavior_risk="LOW",
                overall_risk="LOW", confidence="HIGH", input_manifest=manifest(kind="token_risk_snapshots", parameters={}, facts={})))
            dataset, _ = HistoricalDatasetBuilder(session).materialize(cutoff, cutoff, dataset_version="study", commit=False)
            candidate = RadarEngine(session).ingest(pair, at=cutoff)[0]
            signal = LiveSignalService(session).materialize(candidate, as_of=cutoff)
            observation = record_observation(session, candidate, "WATCH", at=cutoff)
            evaluate_due(session, at=cutoff + timedelta(seconds=35))
            outcome = session.scalar(select(LiveSignalOutcome).where(LiveSignalOutcome.horizon == "30s"))
            old_ids = wallet.id, token.id, dataset.id, signal.id, observation.id, outcome.id, risk.id
            session.commit()
        result = alembic("upgrade", "head")
        assert result.returncode == 0, result.stderr
        with Session(engine) as session:
            for model in (WalletSmartMoneySnapshot, TokenSmartMoneySnapshot, BacktestDataset, Signal, LiveSignalObservation, LiveSignalOutcome):
                assert session.scalar(select(model)) is None
            assert session.scalar(select(TokenRiskSnapshot.id)) == old_ids[-1]  # unrelated recipes stay usable
            from smms.research.rebuild import _repair
            _repair(session, epoch=1, now=cutoff + timedelta(days=2), batch_size=32)
            session.commit()
        with Session(engine) as session:
            for step in range(12):
                rebuild_due(session, now=cutoff + timedelta(days=2, minutes=step), batch_size=32)
                session.commit()
            current = session.scalar(select(TokenSmartMoneySnapshot))
            assert current.smart_money_net_flow == 10 and current.previous_snapshot_id == old_ids[1]
            assert current.input_manifest["parameters"]["input_policy"] == "canonical-token-usd-v2"
            assert Decimal(load_materialized_dataset(session, "study")[0]["smart_money"]["net_flow"]) == 10
            assert session.scalar(select(Signal)).id != old_ids[3]
            assert session.scalar(select(LiveSignalOutcome).where(LiveSignalOutcome.horizon == "30s")).return_pct == 1
            old = session.scalar(select(TokenSmartMoneySnapshot).where(TokenSmartMoneySnapshot.id == old_ids[1]).execution_options(include_invalid_snapshots=True))
            assert old.smart_money_net_flow == 999
            old_wallet = session.scalar(select(WalletSmartMoneySnapshot).where(WalletSmartMoneySnapshot.id == old_ids[0]).execution_options(include_invalid_snapshots=True))
            assert old_wallet.profile_json["roi"] == "999"
            entry = session.scalar(select(TradeEvent).where(TradeEvent.tx_hash == "entry"))
            entry.value_usd, entry.price_usd, entry.native_amount = None, None, 1000
            IntelligenceAggregationService(session, min_wallet_score=0).refresh_token("token", as_of=cutoff)
            session.commit()
            assert session.scalar(select(TokenSmartMoneySnapshot)).smart_money_net_flow is None
        refused = alembic("downgrade", "20261106_0042")
        assert refused.returncode != 0 and "refusing lossy downgrade" in refused.stderr
        assert "20261106_0043" in alembic("current").stdout
    finally:
        engine.dispose()
        try:
            with owner.begin() as connection:
                connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        finally:
            owner.dispose()

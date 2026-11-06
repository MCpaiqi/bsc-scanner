"""Quarantine real pre-lineage Radar/Signal values and reconstruct after restart."""

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
    RadarCandidate,
    RadarCandidateEvidence,
    RadarFeatureObservation,
    Signal,
    Token,
    TokenSmartMoneySnapshot,
    TradeEvent,
)
from smms.radar.views import candidate_view
from smms.research.lineage import manifest
from smms.research.rebuild import rebuild_due

URL = os.getenv("SMMS_MIGRATION_TEST_DATABASE_URL")
ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(not URL, reason="explicit isolated PostgreSQL migration URL required")
def test_legacy_radar_and_signal_values_are_preserved_and_reconstructed():
    owner = create_engine(URL)
    assert owner.dialect.name == "postgresql"
    schema = "smms_radar_" + uuid.uuid4().hex
    with owner.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    engine = create_engine(URL, connect_args={"options": f"-csearch_path={schema}"})
    env = dict(os.environ, DATABASE_URL=URL, PGOPTIONS=f"-csearch_path={schema}", ENABLE_LIVE_TRADING="false")

    def alembic(*args):
        return subprocess.run([sys.executable, "-m", "alembic", *args], cwd=ROOT, env=env,
                              capture_output=True, text=True, timeout=60, check=False)

    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    cutoff = t0 + timedelta(seconds=20)
    try:
        result = alembic("upgrade", "20261106_0039")
        assert result.returncode == 0, result.stderr
        with Session(engine) as session:
            token = Token(chain="bsc", address="token")
            pair = Pair(chain_id=56, address="pair", token0_address="token", token1_address="quote",
                        quote_token="quote", creation_timestamp=t0, creation_block=100)
            session.add_all([token, Token(chain="bsc", address="quote"), pair,
                             TradeEvent(chain="bsc", token_address="token", pair_address="pair", trader_address="wallet",
                                        tx_hash="canonical", timestamp=t0 + timedelta(seconds=10), block_number=101,
                                        side="BUY", token_amount=2, value_usd=10, price_usd=5, source="fixture")])
            session.commit()
            token_id, pair_id = token.id, pair.id
        with engine.begin() as connection:
            metadata = MetaData()
            wallets = Table("wallet_smart_money_snapshots", metadata, autoload_with=connection)
            wallet_manifest = manifest(kind="wallet_smart_money_snapshots", parameters={}, facts={"legacy_fixture": True})
            wallet_id = connection.execute(wallets.insert().values(
                chain="bsc", wallet_address="wallet", score=99, confidence="HIGH", confidence_score=99,
                score_version="v2", profile_version="v1", as_of=cutoff, sample_size=99,
                data_completeness=1, historical_coverage=1, trade_quality=1, profile_json={}, components_json={},
                contributions_json={}, updated_at=cutoff, source_epoch=1, input_manifest=wallet_manifest,
            ).returning(wallets.c.id)).scalar_one()
            tokens = Table("token_smart_money_snapshots", metadata, autoload_with=connection)
            connection.execute(tokens.insert().values(
                chain="bsc", token_address="token", as_of=cutoff, score_version="v2", smart_wallet_count=99,
                smart_money_buy_usd=999, smart_money_sell_usd=0, smart_money_net_flow=999, early_smart_buy_count=99,
                smart_money_score=99, confidence="HIGH", confidence_score=99, data_status="OK", top_smart_wallets_json=[],
                updated_at=cutoff, source_epoch=1, input_manifest=manifest(
                    kind="token_smart_money_snapshots", parameters={"min_wallet_score": "0"}, facts={},
                    dependencies=[{"kind": "wallet_smart_money_snapshots", "id": wallet_id, "digest": wallet_manifest["digest"]}],
                ),
            ))
            candidates = Table("radar_candidates", metadata, autoload_with=connection)
            candidate_id = connection.execute(candidates.insert().values(
                token_id=token_id, discovered_at=t0, discovery_source="PAIR_CREATED", discovery_reason="legacy",
                latest_seen_at=cutoff, status="WATCH", stage="DEX_PAIR_DISCOVERED", priority_score=99,
                features_json={"smart_money_score": "99", "risk": "LOW"}, priority_json={}, data_status="OBSERVED",
                created_at=t0, updated_at=cutoff, revision=1,
            ).returning(candidates.c.id)).scalar_one()
            signals = Table("signals", metadata, autoload_with=connection)
            connection.execute(signals.insert().values(
                token_id=token_id, created_at=cutoff, signal_type="STRONG", signal_strength=99, wallet_count=3,
                smart_money_score=99, market_score=99, entry_reasons=[], risk_flags=[], data_status="OK",
                model_version="signal-v1", config_version="config-v1", config_hash="legacy", status="OPEN", metadata_json={},
            ))
        with Session(engine) as session:
            session.add(RadarCandidateEvidence(candidate_id=candidate_id, source_key=f"pair:{pair_id}",
                                                source_kind="PAIR_CREATED", pair_id=pair_id, raw_required=False, valid=True))
            session.commit()
        result = alembic("upgrade", "head")
        assert result.returncode == 0, result.stderr
        with Session(engine) as session:
            candidate = session.get(RadarCandidate, candidate_id)
            assert candidate_view(session, candidate)["data_status"] == "REBUILDING"
            assert session.scalar(select(Signal)) is None
            assert session.scalar(select(TokenSmartMoneySnapshot)) is None  # same-epoch dependency backfill is quarantined
            legacy = session.scalar(select(RadarFeatureObservation).execution_options(include_invalid_snapshots=True))
            assert legacy.features_json["smart_money_score"] == "99"
            assert legacy.context_json["stage"] == "DEX_PAIR_DISCOVERED"
            assert legacy.context_json["status"] == "WATCH"
            rebuild_due(session, now=cutoff + timedelta(days=1), batch_size=1)
            session.commit()
        with Session(engine) as session:
            for _ in range(8):
                rebuild_due(session, now=cutoff + timedelta(days=1), batch_size=1)
                session.commit()
            current = session.scalar(select(Signal))
            assert current is not None and current.source_epoch == 1 and current.smart_money_score is None
            assert current.input_manifest["parameters"]["reconstruction_policy"] == "legacy-defaults-v1"
            old = session.scalar(select(Signal).where(Signal.research_revision == 0)
                                  .execution_options(include_invalid_snapshots=True))
            assert old.signal_strength == 99 and old.snapshot_status == "SUPERSEDED"
            assert candidate_view(session, session.get(RadarCandidate, candidate_id))["features"]["smart_money_score"] == "UNKNOWN"
        refused = alembic("downgrade", "20261106_0039")
        assert refused.returncode != 0 and "refusing lossy downgrade" in refused.stderr
        assert "20261106_0040" in alembic("current").stdout
    finally:
        engine.dispose()
        try:
            with owner.begin() as connection:
                connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        finally:
            owner.dispose()

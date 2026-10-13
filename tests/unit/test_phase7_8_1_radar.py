from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from smms.config.settings import Settings
from smms.db.models import (
    Base,
    MarketEvent,
    Pair,
    RadarCandidate,
    RadarCandidateEvidence,
    RadarCandidateHistory,
    RawChainEvent,
    Token,
    TokenLifecycleEvent,
    TradeEvent,
)
from smms.radar.config import RadarConfig
from smms.radar.domain import CandidateStatus as S
from smms.radar.engine import RadarEngine
from smms.radar.priority import priority
from smms.radar.views import candidates

T = datetime(2026, 9, 12, 12, tzinfo=UTC)


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def token(session, address="meme"):
    value = Token(chain="bsc", address=address, first_seen_at=T)
    session.add(value)
    session.flush()
    return value


def pair(session, address="meme", tx="pair-tx"):
    value = Pair(
        chain_id=56,
        address=f"pair-{address}",
        token0_address=address,
        token1_address="bnb",
        quote_token="bnb",
        creation_tx_hash=tx,
        creation_timestamp=T,
        creation_block=10,
    )
    session.add(value)
    session.flush()
    return value


def raw(session, tx="pair-tx"):
    value = RawChainEvent(
        chain_id=56,
        transaction_hash=tx,
        log_index=0,
        block_number=10,
        block_hash="hash-a",
        contract_address="factory",
        data="0x",
        timestamp=T,
        provider="fixture",
        removed=False,
        confirmation_status="confirmed",
    )
    session.add(value)
    session.flush()
    return value


def test_pair_creation_idempotent_and_unknown_features(session):
    t = token(session)
    p = pair(session)
    engine = RadarEngine(session)
    first = engine.ingest(p, at=T)[0]
    again = engine.ingest(p, at=T)[0]
    assert first.id == again.id and first.token_id == t.id
    assert first.status == S.WATCH
    assert first.discovery_source == "PAIR_CREATED"
    assert first.features_json["buy_count_5m"] == "UNKNOWN"
    assert first.features_json["known_smart_wallet_count"] == "UNKNOWN"
    assert first.features_json["early_buyer_count"] == "UNKNOWN"
    assert session.query(RadarCandidateEvidence).count() == 1
    assert session.query(RadarCandidateHistory).count() == 3


def test_launchpad_created_before_pair_and_state_transitions(session):
    token(session)
    creation = TokenLifecycleEvent(
        chain="bsc",
        token_address="meme",
        launchpad="flap",
        timestamp=T,
        event_type="CREATED",
        tx_hash="create",
        block_number=10,
    )
    session.add(creation)
    candidate = RadarEngine(session).ingest(creation, at=T)[0]
    assert session.query(Pair).count() == 0
    assert candidate.stage == "LAUNCHPAD_DISCOVERED"
    with pytest.raises(AttributeError):
        candidate.status = "HIGH_SIGNAL"
    with pytest.raises(TypeError):
        candidate.transition("REJECTED", reason="test", at=T)
    history = candidate.transition(S.REJECTED, reason="test rejection", at=T)
    assert history.previous_status == S.WATCH and history.new_status == S.REJECTED
    with pytest.raises(ValueError):
        candidate.transition(S.HIGH_SIGNAL, reason="illegal", at=T)


def test_orphan_rejects_and_reinclusion_restores(session):
    token(session)
    p, r = pair(session), raw(session)
    engine = RadarEngine(session)
    candidate = engine.ingest(p, at=T)[0]
    r.removed = True
    engine.revalidate_raw(r, at=T + timedelta(seconds=1))
    assert candidate.status == S.REJECTED
    assert candidate.priority_score == 0 and candidate.data_status == "ORPHANED"
    assert engine.ingest(p, at=T + timedelta(seconds=2)) == []
    r.removed = False
    engine.ingest(p, at=T + timedelta(seconds=3))
    assert candidate.status == S.WATCH
    assert session.query(RadarCandidate).count() == 1


def test_reorg_alternate_evidence_retains_candidate_but_clears_phase(session):
    token(session)
    p, r = pair(session), raw(session)
    launch = TokenLifecycleEvent(
        chain="bsc",
        token_address="meme",
        launchpad="flap",
        timestamp=T,
        event_type="CREATED",
        tx_hash="create",
    )
    session.add(launch)
    engine = RadarEngine(session)
    candidate = engine.ingest(p, at=T)[0]
    engine.ingest(launch, at=T)
    r.removed = True
    engine.revalidate_raw(r, at=T + timedelta(seconds=1))
    assert candidate.status == S.WATCH and candidate.stage == "LAUNCHPAD_DISCOVERED"
    assert candidate.discovery_source == "LAUNCHPAD_CREATED"


def test_changed_block_hash_requires_renormalization(session):
    token(session)
    p, r = pair(session), raw(session)
    engine = RadarEngine(session)
    candidate = engine.ingest(p, at=T)[0]
    r.block_hash = "hash-b"
    engine.recover(at=T)
    assert candidate.status == S.REJECTED
    p.metadata_json = {"block_hash": "hash-b", "log_index": 0}
    engine.ingest(p, at=T)
    assert candidate.status == S.WATCH


def test_deleted_source_rejected_and_restart_persists(tmp_path):
    url = f"sqlite:///{(tmp_path / 'radar.db').as_posix()}"
    db = create_engine(url)
    Base.metadata.create_all(db)
    with Session(db) as session:
        token(session)
        p = pair(session)
        RadarEngine(session).ingest(p, at=T)
        session.commit()
    db.dispose()
    db = create_engine(url)
    with Session(db) as session:
        engine = RadarEngine(session)
        engine.recover(at=T + timedelta(seconds=10))
        assert session.query(RadarCandidate).count() == 1
        candidate = session.scalar(select(RadarCandidate))
        session.delete(session.scalar(select(Pair)))
        session.flush()
        engine.refresh(candidate, at=T + timedelta(seconds=11))
        assert candidate.status == S.REJECTED
    db.dispose()


def test_first_meaningful_buy_market_activity_and_dedup_counts(session):
    token(session)
    trade = TradeEvent(
        chain="bsc",
        token_address="meme",
        trader_address="alice",
        tx_hash="buy",
        event_index=0,
        timestamp=T,
        side="BUY",
        native_amount=Decimal("0.02"),
        liquidity=500,
        market_cap=1000,
        source="flap.rpc",
    )
    session.add(trade)
    engine = RadarEngine(session)
    candidate = engine.ingest(trade, at=T)[0]
    assert candidate.discovery_source == "FIRST_TRADE"
    market = MarketEvent(
        chain_id=56,
        token_address="meme",
        event_type="SWAP",
        tx_hash="buy",
        event_index=0,
        timestamp=T,
        side="BUY",
        usd_value=20,
        source="bsc:pancakeswap",
    )
    session.add(market)
    engine.ingest(market, at=T)
    assert candidate.features_json["buy_count_1m"] == 1
    assert candidate.features_json["sell_count_5m"] == 0
    assert candidate.features_json["unique_buyers_5m"] == 1
    assert Decimal(candidate.features_json["liquidity"]) == 500
    engine.refresh(candidate, at=T + timedelta(hours=2))
    assert candidate.status == S.STALE
    assert candidates(session, engine.config, 56, at=T + timedelta(hours=2))["count"] == 0


def test_priority_deterministic_configurable_and_explained(monkeypatch):
    f = {
        "age_seconds": 0,
        "buy_count_5m": 3,
        "sell_count_5m": 1,
        "smart_money_score": 80,
        "liquidity": 50000,
        "risk": "HIGH",
    }
    config = RadarConfig()
    result = priority(f, config)
    assert result == priority(f, config)
    assert result["priority_score"] == "47.00"  # 20 + 15 + 32 + 10 - 30
    assert priority(f, RadarConfig(high_risk_penalty=0))["priority_score"] == "77.00"
    assert priority({}, config)["components"]["smart_money"] == "UNKNOWN"
    monkeypatch.setenv("RADAR_CONFIG", '{"min_trade_usd": 25}')
    assert Settings(_env_file=None).radar.min_trade_usd == 25


@pytest.mark.asyncio
async def test_async_pipeline_and_api_without_external_feed(tmp_path, monkeypatch):
    import smms.api.radar as api
    from smms.api.app import app
    from smms.db.repositories.async_events import AsyncTokenLifecycleEventRepository
    from smms.market.pipeline import BSCMarketPipeline
    from smms.market.protocols import PancakeSwapAdapter

    db = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'api.db').as_posix()}")
    async with db.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(db, expire_on_commit=False)
    now = datetime.now(UTC)
    async with factory() as session:
        event = RawChainEvent(
            chain_id=56,
            transaction_hash="create",
            log_index=0,
            block_number=1,
            block_hash="hash",
            contract_address="factory",
            topics=[
                PancakeSwapAdapter().config.pair_created_topic,
                "0x" + f"{1:064x}",
                "0x" + f"{2:064x}",
            ],
            data="0x" + f"{3:064x}" + f"{1:064x}",
            timestamp=now,
            provider="fixture",
            removed=False,
        )
        session.add(event)
        await session.flush()
        pipeline = BSCMarketPipeline(session)
        await pipeline.process_raw_event(event)
        await pipeline.process_raw_event(event)
        assert len((await session.scalars(select(RadarCandidate))).all()) == 2
        flap = Token(chain="bsc", address="flap", launchpad="flap", first_seen_at=now)
        session.add(flap)
        await session.flush()
        await AsyncTokenLifecycleEventRepository(session).add_idempotent(
            TokenLifecycleEvent(
                chain="bsc",
                token_address="flap",
                launchpad="flap",
                event_type="CREATED",
                timestamp=now,
                tx_hash="flap-create",
            )
        )
        await session.commit()

    @asynccontextmanager
    async def scope(*args):
        async with factory() as session:
            yield session

    monkeypatch.setattr(api, "session_scope", scope)
    monkeypatch.setattr(api, "get_settings", lambda: Settings(_env_file=None))
    with TestClient(app) as client:
        response = client.get("/api/radar/candidates?launchpad=flap&risk=UNKNOWN&age=3600&limit=1")
        assert response.status_code == 200
        assert response.json()["count"] == 1
        view = response.json()["candidates"][0]
        assert "token_id" not in view and "features_json" not in view
        assert view["latest_signal_class"] == "UNKNOWN"
        assert client.get("/api/radar/candidates/bsc/flap").status_code == 200
        assert client.get("/api/radar/candidates/bsc/missing").status_code == 404
        assert client.get("/api/radar/candidates?status=QUEUED").status_code == 422
    await db.dispose()


@pytest.mark.asyncio
async def test_catchup_pipeline_can_defer_radar_projection(tmp_path, monkeypatch):
    """Catch-up persists canonical facts without recomputing Radar per log."""
    from smms.db.models import RadarCandidate
    from smms.market.pipeline import BSCMarketPipeline
    from smms.market.protocols import PancakeSwapAdapter

    db = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'defer.db').as_posix()}")
    async with db.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    now = datetime.now(UTC)
    async with async_sessionmaker(db, expire_on_commit=False)() as session:
        event = RawChainEvent(
            chain_id=56,
            transaction_hash="defer-create",
            log_index=0,
            block_number=1,
            block_hash="defer-hash",
            contract_address="factory",
            topics=[
                PancakeSwapAdapter().config.pair_created_topic,
                "0x" + f"{1:064x}",
                "0x" + f"{2:064x}",
            ],
            data="0x" + f"{3:064x}" + f"{1:064x}",
            timestamp=now,
            provider="fixture",
            removed=False,
        )
        session.add(event)
        await session.flush()
        pipeline = BSCMarketPipeline(session, defer_radar_projection=True)
        await pipeline.process_raw_event(event)
        assert await session.scalar(select(func.count()).select_from(RadarCandidate)) == 0
    await db.dispose()


@pytest.mark.parametrize("event_type", ["TRADING_STARTED", "NEAR_GRADUATION", "GRADUATED"])
def test_real_flap_lifecycle_vocabulary(session, event_type):
    token(session)
    event = TokenLifecycleEvent(
        chain="bsc",
        token_address="meme",
        launchpad="flap",
        event_type=event_type,
        timestamp=T,
        tx_hash="lifecycle",
    )
    session.add(event)
    candidate = RadarEngine(session).ingest(event, at=T)[0]
    assert candidate.discovery_source == "LAUNCHPAD_LIFECYCLE"
    assert candidate.stage == (
        "LAUNCHPAD_GRADUATED" if event_type == "GRADUATED" else "LAUNCHPAD_ACTIVE"
    )


def test_external_market_observation_cannot_select_radar(session):
    token(session)
    event = MarketEvent(
        chain_id=56,
        token_address="meme",
        event_type="SWAP",
        tx_hash="external",
        timestamp=T,
        side="BUY",
        usd_value=9999,
        source="dexscreener",
    )
    session.add(event)
    assert RadarEngine(session).ingest(event, at=T) == []
    assert session.query(RadarCandidate).count() == 0


def test_future_snapshots_do_not_change_current_signal_or_priority(session):
    from smms.db.models import Signal, TokenRiskSnapshot, TokenSmartMoneySnapshot

    t = token(session)
    p = pair(session)
    engine = RadarEngine(session)
    candidate = engine.ingest(p, at=T)[0]
    score = candidate.priority_score
    session.add(
        Signal(
            token_id=t.id,
            created_at=T + timedelta(seconds=1),
            signal_type="STRONG",
            signal_strength=99,
        )
    )
    session.add(
        TokenRiskSnapshot(
            chain="bsc",
            token_address="meme",
            contract_risk="HIGH",
            liquidity_risk="HIGH",
            holder_risk="HIGH",
            behavior_risk="HIGH",
            overall_risk="HIGH",
            confidence="HIGH",
            as_of=T + timedelta(seconds=1),
            risk_version="risk-v1",
        )
    )
    session.add(
        TokenSmartMoneySnapshot(
            chain="bsc",
            token_address="meme",
            smart_wallet_count=4,
            smart_money_net_flow=250,
            smart_money_score=90,
            confidence="HIGH",
            confidence_score=90,
            data_status="OK",
            as_of=T + timedelta(seconds=1),
            score_version="v2",
        )
    )
    session.flush()
    engine.refresh(candidate, at=T)
    assert candidate.latest_signal_id is None and candidate.priority_score == score
    engine.refresh(candidate, at=T + timedelta(seconds=2))
    assert candidate.status == S.HIGH_RISK
    assert candidate.features_json["known_smart_wallet_count"] == 4
    assert candidate.latest_signal_id is not None


def test_raw_deletion_and_ambiguous_legacy_pair_fail_closed(session):
    token(session)
    p, r = pair(session), raw(session)
    engine = RadarEngine(session)
    candidate = engine.ingest(p, at=T)[0]
    session.delete(r)
    session.flush()
    engine.refresh(candidate, at=T)
    assert candidate.status == S.REJECTED
    r1 = raw(session)
    session.add(
        RawChainEvent(
            chain_id=56,
            transaction_hash=r1.transaction_hash,
            log_index=1,
            block_number=10,
            block_hash="hash-a",
            contract_address="factory",
            data="0x",
            timestamp=T,
            provider="fixture",
            removed=True,
            confirmation_status="orphaned",
        )
    )
    session.flush()
    assert engine.ingest(p, at=T) == []


def test_radar_migration_can_store_projection(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect

    from smms.config.settings import get_settings

    url = f"sqlite:///{(tmp_path / 'migration.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    config = Config("alembic.ini")
    try:
        command.upgrade(config, "head")
        db = create_engine(url)
        with Session(db) as session:
            token(session)
            RadarEngine(session).ingest(pair(session), at=T)
            session.commit()
            assert session.query(RadarCandidate).count() == 1
        assert "raw_required" in {
            c["name"] for c in inspect(db).get_columns("radar_candidate_evidence")
        }
        command.downgrade(config, "20260912_0014")
        assert "radar_candidates" not in inspect(db).get_table_names()
        db.dispose()
    finally:
        get_settings.cache_clear()

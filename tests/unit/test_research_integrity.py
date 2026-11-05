"""Cross-module regressions for the BSC research desk's canonical inputs."""

import asyncio
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from smms.backtest.dataset import HistoricalDatasetBuilder, load_materialized_dataset
from smms.backtest.signal_engine import run_backtest
from smms.chains.base import Block, ChainLog
from smms.config import Settings
from smms.db.models import (
    Base,
    MarketEvent,
    Pair,
    RawChainEvent,
    StreamCheckpoint,
    Token,
    TokenLifecycleEvent,
    TokenRiskSnapshot,
    TradeEvent,
)
from smms.ingestion.bsc_stream import BSCRealtimeIngestionService
from smms.risk.aggregation import RiskAggregationService

T0 = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def research_session():
    # PostgreSQL verification is explicit and never falls back to a local .env.
    # Each test owns a fresh schema; pre-existing tables/data are never dropped.
    owner = create_engine(os.getenv("SMMS_RESEARCH_TEST_DATABASE_URL", "sqlite://"))
    schema = "smms_research_" + uuid.uuid4().hex if owner.dialect.name == "postgresql" else None
    if schema:
        with owner.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        engine = owner.execution_options(schema_translate_map={None: schema})
    else:
        engine = owner
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            yield session
    finally:
        if schema:
            with owner.begin() as connection:
                connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        owner.dispose()


def seed_market(session):
    token = Token(chain="bsc", address="research-token", creation_timestamp=T0)
    session.add_all([
        token,
        Pair(chain_id=56, address="research-pair", token0_address="research-token",
             token1_address="quote", creation_timestamp=T0),
        TradeEvent(chain="bsc", token_address="research-token", trader_address="buyer",
                   tx_hash="entry", timestamp=T0, side="BUY", price=1,
                   liquidity=100_000, source="fixture"),
    ])
    session.flush()
    return token


def seed_risk(session, level):
    session.add(TokenRiskSnapshot(
        chain="bsc", token_address="research-token", contract_risk=level,
        liquidity_risk=level, holder_risk=level, behavior_risk=level,
        overall_risk=level, confidence="HIGH", as_of=T0, risk_version="risk-v1",
    ))
    session.flush()


@pytest.mark.asyncio
async def test_background_reorg_invalidates_orphan_then_allows_next_log():
    """Nested acquisition must not stall reconciliation or subsequent ingestion."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    checked = asyncio.Event()

    class Provider:
        async def get_latest_block_number(self):
            return 100

        async def get_block(self, number, *, include_transactions=False):
            checked.set()
            return Block(number=number, block_hash="0xcanonical", timestamp=T0)

    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            pair = Pair(
                chain_id=56, address="0x" + "1" * 40,
                token0_address="0x" + "2" * 40, token1_address="0x" + "3" * 40,
                creation_block=100, creation_block_hash="0xorphan",
                creation_timestamp=T0,
            )
            session.add(pair)
            await session.commit()
            settings = Settings(_env_file=None, BSC_REORG_LOOKBACK_BLOCKS=1)
            service = BSCRealtimeIngestionService(
                SimpleNamespace(settings=settings), session=session, chain_provider=Provider(),
            )
            service._reorg_reconcile_task = asyncio.create_task(
                service._run_reorg_reconciler(settings)
            )
            try:
                await asyncio.wait_for(checked.wait(), timeout=2)
                # Wait for the real reconciliation transaction, without concurrent
                # operations on its AsyncSession, then inspect its durable effect.
                async with asyncio.timeout(2):
                    async with service._db_lock:
                        await session.refresh(pair)
                        assert pair.canonical is False
                        assert pair.removed is True
                raw = await asyncio.wait_for(service.process_log(ChainLog(
                    address="0x" + "4" * 40, topics=(), data="0x", block_number=101,
                    block_hash="0xnext", transaction_hash="0xnew-log", log_index=0,
                    timestamp=T0,
                )), timeout=2)
                assert raw.transaction_hash == "0xnew-log"
                assert raw.removed is False
                await service.close()
                assert not service._db_lock.locked()
            finally:
                await service.close()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_cancelling_background_probe_releases_ingestion_lock():
    entered = asyncio.Event()

    class Provider:
        async def get_latest_block_number(self):
            entered.set()
            await asyncio.Event().wait()

    # No DB operation is reached: cancellation happens inside the provider call.
    service = BSCRealtimeIngestionService(SimpleNamespace(), session=object(), chain_provider=Provider())
    service._reorg_reconcile_task = asyncio.create_task(
        service._run_reorg_reconciler(SimpleNamespace())
    )
    task = service._reorg_reconcile_task
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        await service.close()
        async with asyncio.timeout(2):
            async with service._db_lock:
                assert task.cancelled()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_run_commits_live_log_before_background_db_work_and_drains_shutdown():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    commit_entered, probe_started, reorg_entered = (
        asyncio.Event(), asyncio.Event(), asyncio.Event()
    )
    timeline = []
    state = {"live": False, "gated": False, "task": None}
    log = ChainLog(address="0x" + "4" * 40, topics=(), data="0x", block_number=101,
                   transaction_hash="0xlive-owned", log_index=0, timestamp=T0,
                   block_hash="0xcanonical")

    class Stream:
        settings = Settings(_env_file=None, BSC_DEX_FACTORY_ADDRESSES="0x" + "3" * 40,
                            BSC_REORG_LOOKBACK_BLOCKS=1, BSC_LOG_CHUNK_SIZE=1)

        async def subscribe_logs(self, **_kwargs):
            state["live"] = True
            yield log
            await asyncio.wait_for(reorg_entered.wait(), timeout=2)

    class Provider:
        async def get_latest_block_number(self):
            if asyncio.current_task() is not state["task"]:
                await commit_entered.wait()
                probe_started.set()
            return 101

        async def get_logs(self, **_kwargs):
            return []

        async def get_block(self, number, *, include_transactions=False):
            if state["live"] and not reorg_entered.is_set():
                timeline.append("reorg_db")
                reorg_entered.set()
            return Block(number=number, block_hash="0xcanonical", timestamp=T0)

    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            stream = Stream()
            service = BSCRealtimeIngestionService(stream, session=session, chain_provider=Provider())
            service._build_dynamic_stream = lambda *_args: stream
            original_commit = session.commit

            async def gated_commit():
                if (state["live"] and not state["gated"]
                        and asyncio.current_task() is state["task"]):
                    state["gated"] = True
                    timeline.append("commit_enter")
                    commit_entered.set()
                    await asyncio.wait_for(probe_started.wait(), timeout=2)
                    # Force the interleaving: a correctly owned transaction
                    # excludes background DB work until the real commit returns.
                    try:
                        await asyncio.wait_for(reorg_entered.wait(), timeout=0.05)
                    except TimeoutError:
                        pass
                    await original_commit()
                    timeline.append("commit_done")
                else:
                    await original_commit()

            session.commit = gated_commit
            state["task"] = asyncio.create_task(service.run())
            try:
                await asyncio.wait_for(state["task"], timeout=5)
                assert timeline[:3] == ["commit_enter", "commit_done", "reorg_db"]
                raw = await session.scalar(select(RawChainEvent))
                checkpoint = await session.scalar(select(StreamCheckpoint).where(
                    StreamCheckpoint.stream_name == "bsc_realtime",
                ))
                assert raw.transaction_hash == "0xlive-owned"
                assert checkpoint.last_processed_block == 101
                assert checkpoint.status == "stopped"
            finally:
                await service.close()
    finally:
        await engine.dispose()


@pytest.mark.parametrize("level", ["LOW", "CRITICAL", "UNKNOWN"])
def test_builder_serialization_preserves_risk_in_backtest(research_session, level):
    seed_market(research_session)
    seed_risk(research_session, level)
    rows, _ = HistoricalDatasetBuilder(research_session).build(T0, T0)
    payload = json.loads(json.dumps(rows[0].as_dict()))

    signal = run_backtest([payload]).signals[0]

    assert signal.risk_level == level
    assert payload["risk"]["overall_risk"] == level
    assert payload["risk"]["level"] == level  # existing export readers remain valid
    if level == "CRITICAL":
        assert signal.signal == "HIGH_RISK"
        assert signal.entry_eligible is False


def test_materialized_dataset_preserves_critical_risk(research_session):
    seed_market(research_session)
    seed_risk(research_session, "CRITICAL")
    dataset, _ = HistoricalDatasetBuilder(research_session).materialize(
        T0, T0, dataset_version="critical-risk-regression",
    )

    result = run_backtest(load_materialized_dataset(research_session, dataset.id))

    assert len(result.signals) == 1
    assert result.signals[0].risk_level == "CRITICAL"
    assert result.signals[0].signal == "HIGH_RISK"
    assert result.signals[0].entry_eligible is False


@pytest.mark.parametrize("field", ["overall_risk", "risk_level", "level"])
def test_backtest_reads_legacy_and_current_critical_risk_fields(field):
    result = run_backtest([{
        "token": "legacy-token", "timestamp": T0.isoformat(),
        "market": {"price": 1, "score": 100, "liquidity": 100_000},
        "smart_money": {"score": 100},
        "risk": {field: "CRITICAL", "as_of": T0.isoformat()},
    }], threshold=Decimal(1))

    assert result.signals[0].risk_level == "CRITICAL"
    assert result.signals[0].signal == "HIGH_RISK"
    assert result.signals[0].entry_eligible is False


@pytest.mark.parametrize("canonical,removed", [(False, False), (True, True), (False, True)])
def test_orphan_pair_cannot_admit_token_to_research_universe(
    research_session, canonical, removed,
):
    seed_market(research_session)
    pair = research_session.scalar(select(Pair))
    pair.canonical, pair.removed = canonical, removed
    # A genuine losing token must survive; exclusion is based on facts, not outcome.
    research_session.add_all([
        Token(chain="bsc", address="valid-loser", creation_timestamp=T0, outcome_class="LOSER"),
        Pair(chain_id=56, address="valid-pair", token0_address="valid-loser",
             token1_address="quote", creation_timestamp=T0),
    ])
    research_session.flush()

    rows, report = HistoricalDatasetBuilder(research_session).build(T0, T0)

    assert [row.token for row in rows] == ["valid-loser"]
    assert report.tokens_eligible == 1
    assert report.tokens_excluded == 1


@pytest.mark.parametrize("canonical,removed", [(False, False), (True, True), (False, True)])
def test_orphan_trades_do_not_enter_features_or_future_prices(
    research_session, canonical, removed,
):
    seed_market(research_session)
    for seconds in (10, 40):
        research_session.add(TradeEvent(
            chain="bsc", token_address="research-token", trader_address="orphan-buyer",
            tx_hash=f"orphan-{seconds}", timestamp=T0 + timedelta(seconds=seconds),
            side="BUY", price=99, source="fixture", canonical=canonical, removed=removed,
        ))
    research_session.add(TradeEvent(
        chain="bsc", token_address="research-token", trader_address="valid-buyer",
        tx_hash="valid-future", timestamp=T0 + timedelta(seconds=30),
        side="BUY", price=2, source="fixture",
    ))
    research_session.flush()

    rows, _ = HistoricalDatasetBuilder(research_session).build(T0, T0 + timedelta(seconds=20))

    assert [row.signal_timestamp for row in rows] == [T0]
    assert [Decimal(p["price"]) for p in rows[0].feature_price_history] == [Decimal(1)]
    assert [Decimal(p["price"]) for p in rows[0].evaluation_price_path] == [Decimal(2)]


@pytest.mark.parametrize("marker", [
    {"removed": True}, {"orphaned": True}, {"confirmation_status": "orphaned"},
])
def test_orphan_market_metadata_does_not_enter_price_paths(research_session, marker):
    seed_market(research_session)
    for seconds in (10, 40):
        research_session.add(MarketEvent(
            chain_id=56, event_type="TRADE", token_address="research-token",
            tx_hash=f"orphan-market-{seconds}", timestamp=T0 + timedelta(seconds=seconds),
            price=99, source="fixture", metadata_json=marker,
        ))
    research_session.flush()

    rows, _ = HistoricalDatasetBuilder(research_session).build(T0, T0 + timedelta(seconds=20))

    assert [row.signal_timestamp for row in rows] == [T0]
    assert [Decimal(p["price"]) for p in rows[0].feature_price_history] == [Decimal(1)]
    assert rows[0].evaluation_price_path == ()


@pytest.mark.parametrize("marker", [
    {"removed": True}, {"orphaned": True}, {"confirmation_status": "orphaned"},
])
def test_orphan_lifecycle_cannot_migrate_a_valid_launchpad_token(research_session, marker):
    research_session.add(Token(
        chain="bsc", address="launch-token", launchpad="flap", first_seen_at=T0,
    ))
    research_session.add(TokenLifecycleEvent(
        chain="bsc", token_address="launch-token", launchpad="flap",
        event_type="MIGRATED", timestamp=T0 + timedelta(seconds=10),
        tx_hash="orphan-migration", payload_json=marker,
    ))
    research_session.flush()

    rows, _ = HistoricalDatasetBuilder(research_session).build(T0, T0 + timedelta(seconds=20))

    assert [row.signal_timestamp for row in rows] == [T0]
    assert rows[0].market_lifecycle == "LAUNCHPAD_DISCOVERED"


@pytest.mark.parametrize("canonical,removed", [(False, False), (True, True), (False, True)])
def test_new_risk_snapshot_excludes_orphan_trade_behavior(research_session, canonical, removed):
    seed_market(research_session)
    research_session.add_all([TradeEvent(
        chain="bsc", token_address="research-token", trader_address="orphan-wallet",
        tx_hash=f"wash-{index}", timestamp=T0, side="BUY" if index % 2 else "SELL",
        price=99, native_amount=1, source="fixture", canonical=canonical, removed=removed,
    ) for index in range(24)])
    research_session.flush()

    assert RiskAggregationService(research_session).refresh(as_of=T0) == 1
    snapshot = research_session.scalar(select(TokenRiskSnapshot))

    assert snapshot.dimensions_json["behavior"]["details"]["trade_count"] == 1
    assert "high_frequency" not in snapshot.evidence_json


@pytest.mark.parametrize("canonical,removed", [(False, False), (True, True), (False, True)])
def test_orphan_launchpad_trade_cannot_add_lifecycle_or_price(research_session, canonical, removed):
    research_session.add(Token(
        chain="bsc", address="launch-token", launchpad="flap", first_seen_at=T0,
    ))
    research_session.add(TradeEvent(
        chain="bsc", token_address="launch-token", launchpad="flap",
        trader_address="orphan", tx_hash="orphan-launch-trade", side="BUY", price=99,
        timestamp=T0 + timedelta(seconds=10), source="fixture", canonical=canonical,
        removed=removed,
    ))
    research_session.flush()

    rows, _ = HistoricalDatasetBuilder(research_session).build(T0, T0 + timedelta(seconds=20))

    assert [row.signal_timestamp for row in rows] == [T0]
    assert rows[0].market_lifecycle == "LAUNCHPAD_DISCOVERED"
    assert rows[0].feature_price_history == ()
    assert rows[0].evaluation_price_path == ()


@pytest.mark.parametrize("metadata", [{}, {
    "removed": False, "orphaned": False, "confirmation_status": "confirmed",
}])
def test_unmarked_or_explicitly_valid_market_fact_is_retained(research_session, metadata):
    seed_market(research_session)
    research_session.add(MarketEvent(
        chain_id=56, event_type="TRADE", token_address="research-token",
        tx_hash="valid-market", timestamp=T0 + timedelta(seconds=10), price=2,
        source="fixture", metadata_json=metadata,
    ))
    research_session.flush()

    rows, _ = HistoricalDatasetBuilder(research_session).build(T0, T0 + timedelta(seconds=20))

    assert [row.signal_timestamp for row in rows] == [T0, T0 + timedelta(seconds=10)]
    assert Decimal(rows[-1].market["price"]) == Decimal(2)


def test_canonical_rebuild_has_distinct_reproducibility_identity(research_session):
    """Corrected extraction must not present itself as the old dataset-v2 recipe."""
    current = HistoricalDatasetBuilder(research_session)
    legacy = HistoricalDatasetBuilder(research_session, model_version="dataset-v2")

    assert current.config_hash() != legacy.config_hash()


@pytest.mark.parametrize("marker", [
    {"removed": True}, {"orphaned": True}, {"confirmation_status": "orphaned"},
])
def test_orphan_created_event_cannot_admit_token_via_cached_fields(research_session, marker):
    research_session.add_all([
        Token(chain="bsc", address="orphan-launch", launchpad="flap", first_seen_at=T0),
        TokenLifecycleEvent(chain="bsc", token_address="orphan-launch", launchpad="flap",
                            event_type="CREATED", timestamp=T0, tx_hash="orphan-create",
                            payload_json=marker),
    ])
    research_session.flush()

    rows, report = HistoricalDatasetBuilder(research_session).build(T0, T0)

    assert rows == []
    assert report.tokens_eligible == 0


def test_future_creation_evidence_does_not_erase_legacy_discovery(research_session):
    research_session.add(Token(
        chain="bsc", address="legacy-launch", launchpad="flap", first_seen_at=T0,
    ))
    research_session.flush()
    before, _ = HistoricalDatasetBuilder(research_session).build(T0, T0)
    research_session.add(TokenLifecycleEvent(
        chain="bsc", token_address="legacy-launch", launchpad="flap", event_type="CREATED",
        timestamp=T0 + timedelta(seconds=10), tx_hash="future-create",
        payload_json={"orphaned": True},
    ))
    research_session.flush()

    after, _ = HistoricalDatasetBuilder(research_session).build(T0, T0)

    assert [r.as_dict() for r in before] == [r.as_dict() for r in after]
    assert len(after) == 1

"""Canonical, point-in-time early buyers and immutable accounting revisions."""

from datetime import timedelta

import pytest
from sqlalchemy import select
from test_derived_snapshot_integrity import PAIR, T0, TOKEN, WALLET, fact, seed
from test_research_integrity import research_session as _research_session

from smms.backtest.dataset import HistoricalDatasetBuilder
from smms.db.models import EarlyBuyer, MarketEvent, Pair, WalletTokenPosition
from smms.research.rebuild import rebuild_due
from smms.research.state import invalidate_derived
from smms.smart_money.engine import EarlyBuyerEngine

research_session = _research_session
CUTOFF = T0 + timedelta(seconds=20)


def inputs(session, *, at=CUTOFF):
    from smms.smart_money.replay import load_replay_inputs

    return load_replay_inputs(session, TOKEN, as_of=at)


def test_dataset_ignores_unverified_buyer_cache(research_session):
    seed(research_session)
    fact(research_session)
    research_session.add(EarlyBuyer(chain="bsc", token_address=TOKEN, wallet_address="ghost",
                                    first_buy_tx="orphan", first_buy_timestamp=T0, buy_rank=1))
    research_session.flush()
    rows, _ = HistoricalDatasetBuilder(research_session).build(CUTOFF, CUTOFF)
    assert [b["wallet"] for b in rows[0].early_buyer["items"]] == [WALLET]


@pytest.mark.parametrize("invalidity", ["flags", "json", "parent", "future_creation"])
def test_replay_excludes_invalid_sources(research_session, invalidity):
    seed(research_session)
    trade = fact(research_session)
    pair = research_session.scalar(select(Pair))
    if invalidity == "flags":
        trade.canonical, trade.removed = False, True
    elif invalidity == "json":
        trade.metadata_json = {"orphaned": True}
    elif invalidity == "parent":
        pair.canonical, pair.removed = False, True
    else:
        pair.creation_timestamp = CUTOFF + timedelta(seconds=1)
    research_session.flush()
    assert inputs(research_session).buyers() == []


def test_replay_has_no_position_or_manifest_lookahead(research_session):
    seed(research_session)
    fact(research_session)
    fact(research_session, tx="future", side="SELL", at=CUTOFF + timedelta(seconds=1))
    replay = inputs(research_session, at=CUTOFF + timedelta(seconds=2))
    buyer = replay.buyers(as_of=CUTOFF)[0]
    assert buyer.current_balance == 1 and buyer.sell_count == 0
    assert "future" not in str(replay.input_manifest(as_of=CUTOFF))
    assert replay.buyers()[0].current_balance == 0


def test_market_only_buyer_is_observed_and_duplicates_do_not_double_count(research_session):
    seed(research_session)
    trade = fact(research_session)
    for tx, wallet in ((trade.tx_hash, WALLET), ("market-only", "second")):
        research_session.add(MarketEvent(chain_id=56, token_address=TOKEN, pair_address=PAIR,
                                         tx_hash=tx, event_index=0, timestamp=T0 + timedelta(seconds=10),
                                         block_number=101, wallet_address=wallet, side="BUY",
                                         event_type="SWAP", token_amount=1, usd_value=10, source="fixture"))
    research_session.flush()
    buyers = inputs(research_session).buyers()
    assert {b.wallet_address for b in buyers} == {WALLET, "second"}
    assert next(b for b in buyers if b.wallet_address == WALLET).buy_count == 1


def test_rebuild_withdraws_last_orphan_without_overwriting_history(research_session):
    seed(research_session)
    trade = fact(research_session)
    engine = EarlyBuyerEngine(research_session)
    engine.refresh(TOKEN, as_of=CUTOFF)
    original = research_session.scalar(select(EarlyBuyer))
    position = research_session.scalar(select(WalletTokenPosition))
    assert original is not None and position.current_balance == 1
    trade.canonical, trade.removed = False, True
    invalidate_derived(research_session, reason="TEST_REORG", cause={})
    research_session.commit()
    assert research_session.scalar(select(EarlyBuyer)) is None
    assert research_session.scalar(select(WalletTokenPosition)) is None
    rebuild_due(research_session, now=CUTOFF + timedelta(days=1))
    research_session.commit()
    history = research_session.scalars(select(EarlyBuyer).order_by(EarlyBuyer.research_revision)
                                       .execution_options(include_invalid_snapshots=True)).all()
    assert len(history) == 2
    assert history[0].first_buy_tx == original.first_buy_tx == "one"
    assert history[1].snapshot_status == "UNAVAILABLE" and history[1].previous_snapshot_id == original.id
    assert research_session.scalar(select(EarlyBuyer)) is None
    assert position.current_balance == 1


def test_same_asof_refresh_is_idempotent_and_preserves_changed_result(research_session):
    seed(research_session)
    fact(research_session)
    service = EarlyBuyerEngine(research_session)
    service.refresh(TOKEN, as_of=CUTOFF)
    original = research_session.scalar(select(EarlyBuyer))
    service.refresh(TOKEN, as_of=CUTOFF)
    assert research_session.scalar(select(EarlyBuyer)).id == original.id
    fact(research_session, tx="second", at=T0 + timedelta(seconds=15))
    service.refresh(TOKEN, as_of=CUTOFF)
    current = research_session.scalar(select(EarlyBuyer))
    assert current.id != original.id and current.buy_count == 2 and original.buy_count == 1
    assert current.input_manifest["digest"] != original.input_manifest["digest"]


def test_radar_features_replay_buyer_after_candidate_was_invalidated(research_session):
    from smms.db.models import RadarCandidate
    from smms.radar.engine import RadarEngine

    token = seed(research_session)
    fact(research_session)
    candidate = RadarCandidate(token_id=token.id, discovered_at=T0, latest_seen_at=CUTOFF,
                               discovery_source="PAIR_CREATED", discovery_reason="fixture",
                               stage="DEX_PAIR_DISCOVERED", invalidated_at=T0)
    research_session.add(candidate)
    research_session.flush()
    features = RadarEngine(research_session).features(token, candidate, CUTOFF)
    assert features["early_buyer_count"] == 1


def test_missing_usd_is_not_zero_cost_profit_and_sell_still_reduces_inventory(research_session):
    seed(research_session)
    buy = fact(research_session)
    buy.value_usd = None
    sell = fact(research_session, tx="sale", side="SELL", at=T0 + timedelta(seconds=15))
    sell.value_usd = None
    research_session.flush()
    buyer = inputs(research_session).buyers()[0]
    assert buyer.current_balance == 0
    assert buyer.realized_pnl is None and buyer.total_invested is None and buyer.roi is None
    assert buyer.total_buy_usd is None


def test_missing_quantity_does_not_report_zero_position(research_session):
    seed(research_session)
    trade = fact(research_session)
    trade.token_amount = None
    research_session.flush()
    buyer = inputs(research_session).buyers()[0]
    assert buyer.current_balance is None and buyer.unrealized_pnl is None


def test_same_timestamp_fifo_follows_chain_log_order(research_session):
    seed(research_session)
    buy = fact(research_session, tx="z-buy")
    sell = fact(research_session, tx="a-sell", side="SELL", value=20)
    buy.event_index, sell.event_index = 1, 2
    research_session.flush()
    buyer = inputs(research_session).buyers()[0]
    assert buyer.realized_pnl == 10 and buyer.current_balance == 0


def test_stale_publisher_cannot_replace_new_epoch_results(research_session):
    from smms.smart_money.early_buyers import EarlyBuyerConfig

    seed(research_session)
    trade = fact(research_session)
    stale = inputs(research_session)
    trade.canonical, trade.removed = False, True
    fact(research_session, tx="replacement", value=5)
    invalidate_derived(research_session, reason="TEST_REORG", cause={})
    engine = EarlyBuyerEngine(research_session)
    engine.refresh(TOKEN, as_of=CUTOFF)
    current = research_session.scalar(select(EarlyBuyer))
    engine._publish(stale, EarlyBuyerConfig())
    assert research_session.scalar(select(EarlyBuyer)).id == current.id
    assert current.first_buy_tx == "replacement" and current.snapshot_status == "VALID"


def test_epoch_change_during_read_requires_retry(research_session, monkeypatch):
    from smms.smart_money import replay

    seed(research_session)
    fact(research_session)
    epochs = iter([0, 1])
    monkeypatch.setattr(replay, "capture_epoch", lambda *args: next(epochs))
    with pytest.raises(replay.StaleReplayInputs):
        inputs(research_session)


def test_persisted_manifest_remains_idempotent_after_reload(research_session):
    seed(research_session)
    fact(research_session)
    service = EarlyBuyerEngine(research_session)
    service.refresh(TOKEN, as_of=CUTOFF)
    identity = research_session.scalar(select(EarlyBuyer.id))
    research_session.commit()
    research_session.expire_all()
    service.refresh(TOKEN, as_of=CUTOFF)
    assert research_session.scalar(select(EarlyBuyer.id)) == identity


def test_position_covers_non_early_wallet_and_unknown_quantity(research_session):
    from smms.smart_money.early_buyers import EarlyBuyerConfig

    seed(research_session)
    fact(research_session)
    late = fact(research_session, tx="later", at=T0 + timedelta(seconds=15))
    late.trader_address, late.token_amount = "later-wallet", None
    EarlyBuyerEngine(research_session).refresh(TOKEN, as_of=CUTOFF, config=EarlyBuyerConfig(top_n=1))
    assert len(research_session.scalars(select(EarlyBuyer)).all()) == 1
    positions = research_session.scalars(select(WalletTokenPosition)).all()
    assert len(positions) == 2
    assert next(p for p in positions if p.wallet_address == "later-wallet").current_balance is None


@pytest.mark.asyncio
async def test_api_uses_canonical_token0_pair_and_cutoff(monkeypatch):
    from contextlib import asynccontextmanager
    from importlib import import_module

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from smms.db.models import Base

    api = import_module("smms.api.app")
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def scope(*args):
        async with maker() as session:
            yield session

    try:
        async with maker() as session:
            def populate(sync):
                seed(sync)
                fact(sync)
                bad = fact(sync, tx="orphan", at=T0 + timedelta(seconds=12))
                bad.canonical, bad.removed = False, True
                fact(sync, tx="future", side="SELL", at=CUTOFF + timedelta(seconds=1))
            await session.run_sync(populate)
            await session.commit()
        monkeypatch.setattr(api, "session_scope", scope)
        response = await api.token_early_buyers(TOKEN, limit=20, sort="rank", time_window=None, as_of=CUTOFF)
        assert response["data_status"] == "CHAIN_EVENTS"
        assert len(response["early_buyers"]) == 1
        buyer = response["early_buyers"][0]
        assert buyer["buy_count"] == 1 and buyer["sell_count"] == 0
        assert buyer["entry_usd"] == "10.000000000000000000"
        assert buyer["current_position"] == "1.000000000000000000"
        assert response["replay"]["digest"]
    finally:
        await engine.dispose()


@pytest.mark.parametrize("model", [EarlyBuyer, WalletTokenPosition])
def test_epoch_visibility_includes_aliases_and_published_rows_are_immutable(research_session, model):
    from sqlalchemy.orm import aliased

    seed(research_session)
    fact(research_session)
    EarlyBuyerEngine(research_session).refresh(TOKEN, as_of=CUTOFF)
    row = research_session.scalar(select(model))
    invalidate_derived(research_session, reason="TEST_REORG", cause={})
    research_session.commit()
    assert research_session.scalar(select(aliased(model))) is None
    row.metadata_json = {"altered": True}
    with pytest.raises(ValueError, match="immutable"):
        research_session.flush()


def test_two_postgres_publishers_converge_on_one_buyer_and_position(research_session):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from sqlalchemy.orm import Session

    from smms.smart_money.early_buyers import EarlyBuyerConfig

    if research_session.bind.dialect.name != "postgresql":
        pytest.skip("independent publishers require PostgreSQL")
    seed(research_session)
    fact(research_session)
    research_session.commit()
    ready = Barrier(2)

    def publish():
        with Session(research_session.bind) as session:
            replay = inputs(session)
            ready.wait(timeout=5)
            EarlyBuyerEngine(session)._publish(replay, EarlyBuyerConfig())
            session.commit()

    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(publish) for _ in range(2)]
        for job in jobs:
            job.result(timeout=10)
    for model in (EarlyBuyer, WalletTokenPosition):
        history = research_session.scalars(select(model).execution_options(include_invalid_snapshots=True)).all()
        assert len(history) == 1 and history[0].research_revision == 0


def test_postgres_reorg_commits_while_buyer_publisher_is_in_flight(research_session):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from sqlalchemy import text
    from sqlalchemy.orm import Session

    from smms.smart_money.early_buyers import EarlyBuyerConfig

    if research_session.bind.dialect.name != "postgresql":
        pytest.skip("independent reorg and producer writers require PostgreSQL")
    seed(research_session)
    trade = fact(research_session)
    research_session.commit()
    captured, release = Event(), Event()

    def publish():
        with Session(research_session.bind) as session:
            replay = inputs(session)
            captured.set()
            assert release.wait(timeout=8)
            EarlyBuyerEngine(session)._publish(replay, EarlyBuyerConfig())
            session.commit()

    with ThreadPoolExecutor(max_workers=1) as pool:
        job = pool.submit(publish)
        try:
            assert captured.wait(timeout=5)
            research_session.execute(text("SET LOCAL lock_timeout = '3s'"))
            trade.canonical, trade.removed = False, True
            invalidate_derived(research_session, reason="TEST_REORG", cause={})
            research_session.commit()
        finally:
            release.set()
        job.result(timeout=10)
    assert research_session.scalar(select(EarlyBuyer)) is None
    assert research_session.scalar(select(WalletTokenPosition)) is None


def test_explicit_usd_price_column_marks_position_without_execution_value(research_session):
    seed(research_session)
    trade = fact(research_session)
    trade.price_usd, trade.value_usd = 3, None
    research_session.flush()
    buyer = inputs(research_session).buyers()[0]
    assert buyer.current_price == 3
    assert buyer.total_invested is None and buyer.unrealized_pnl is None


def test_first_buyer_rank_uses_chain_order_before_wallet_address(research_session):
    from smms.smart_money.early_buyers import EarlyBuyerConfig

    seed(research_session)
    first = fact(research_session, tx="z-first")
    first.event_index, first.trader_address = 1, "z-wallet"
    later = fact(research_session, tx="a-later")
    later.event_index, later.trader_address = 2, "a-wallet"
    research_session.flush()
    assert inputs(research_session).buyers(config=EarlyBuyerConfig(top_n=1))[0].wallet_address == "z-wallet"


@pytest.mark.parametrize("decimals", [18, None])
def test_market_raw_units_are_not_mixed_with_normalized_trade_units(research_session, decimals):
    seed(research_session)
    research_session.add(MarketEvent(
        chain_id=56, token_address=TOKEN, pair_address=PAIR, wallet_address=WALLET,
        event_type="TRADE", side="BUY", tx_hash="raw-buy", event_index=1, block_number=101,
        timestamp=T0 + timedelta(seconds=10), token_amount=10**18, usd_value=10, source="fixture",
        metadata_json={"token_decimals": decimals},
    ))
    fact(research_session, tx="sell", side="SELL", at=T0 + timedelta(seconds=15), value=20)
    buyer = inputs(research_session).buyers()[0]
    if decimals is None:
        assert buyer.current_balance is None and buyer.realized_pnl is None
    else:
        assert buyer.current_balance == 0 and buyer.realized_pnl == 10


def test_replay_refreshes_values_retained_in_identity_map(research_session):
    from sqlalchemy.orm import Session

    seed(research_session)
    held = fact(research_session)
    held_pair = research_session.scalar(select(Pair))
    research_session.commit()
    # Populate after commit and retain strong references to both cached rows.
    assert held.value_usd == 10 and held_pair.creation_timestamp is not None
    with Session(research_session.bind) as writer:
        row = writer.scalar(select(type(held)))
        pair = writer.scalar(select(Pair))
        row.value_usd, pair.creation_timestamp = 20, T0 + timedelta(seconds=5)
        invalidate_derived(writer, reason="TEST_CORRECTION", cause={})
        writer.commit()
    replay = inputs(research_session)
    buyer = replay.buyers()[0]
    assert buyer.total_invested == 20 and buyer.entry_delay_seconds == 5


def test_radar_uses_bounded_early_entry_read_with_explicit_unknown_on_overflow(research_session, monkeypatch):
    from smms.db.models import RadarCandidate
    from smms.radar.engine import RadarEngine
    from smms.smart_money import replay

    token = seed(research_session)
    for index in range(3):
        fact(research_session, tx=f"repeated-{index}")
    monkeypatch.setattr(replay, "RADAR_ENTRY_EVENT_LIMIT", 2)
    candidate = RadarCandidate(token_id=token.id, discovered_at=T0, latest_seen_at=CUTOFF,
                               discovery_source="PAIR_CREATED", discovery_reason="fixture",
                               stage="DEX_PAIR_DISCOVERED")
    research_session.add(candidate)
    research_session.flush()
    features = RadarEngine(research_session).features(token, candidate, CUTOFF)
    assert features["early_buyer_count"] == "UNKNOWN"
    assert features["reason_codes"]["early_buyer_count"] == "EARLY_ENTRY_SOURCE_LIMIT"


def test_entry_bound_does_not_hide_a_saturated_top_twenty(research_session, monkeypatch):
    from smms.smart_money import replay

    seed(research_session)
    for index in range(25):
        row = fact(research_session, tx=f"buyer-{index}")
        row.event_index, row.trader_address = index, f"wallet-{index}"
    monkeypatch.setattr(replay, "RADAR_ENTRY_EVENT_LIMIT", 20)
    result = replay.load_early_entries(research_session, TOKEN, as_of=CUTOFF)
    assert result.reason is None and len(result.entries) == 20


def test_entry_bound_tracks_each_source_and_respects_trade_side_priority(research_session, monkeypatch):
    from smms.smart_money import replay

    seed(research_session)
    trade = fact(research_session)
    other = fact(research_session, tx="second")
    other.trader_address = "second-wallet"
    for tx, wallet in (("market", "market-wallet"), ("corrected", "wrong-wallet")):
        research_session.add(MarketEvent(chain_id=56, token_address=TOKEN, pair_address=PAIR,
                                         wallet_address=wallet, tx_hash=tx, event_index=0, block_number=101,
                                         timestamp=trade.timestamp, side="BUY", event_type="TRADE", source="fixture"))
    fact(research_session, tx="corrected", side="SELL")
    monkeypatch.setattr(replay, "RADAR_ENTRY_EVENT_LIMIT", 2)
    result = replay.load_early_entries(research_session, TOKEN, as_of=CUTOFF)
    assert result.reason is None
    assert {row["wallet"] for row in result.entries} == {WALLET, "second-wallet", "market-wallet"}

"""Canonical derived snapshot visibility, immutable history and reconstruction."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from test_alpha_collector import coverage_session as _coverage_session
from test_research_integrity import research_session as _research_session

from smms.db.models import Pair, Token, TokenRiskSnapshot, TokenSmartMoneySnapshot, TradeEvent
from smms.evaluation import utc
from smms.risk.aggregation import RiskAggregationService
from smms.smart_money.aggregation import IntelligenceAggregationService

research_session = _research_session
coverage_session = _coverage_session
T0 = datetime(2026, 1, 1, tzinfo=UTC)
TOKEN, QUOTE, PAIR, WALLET = (f"0x{n:040x}" for n in range(801, 805))


def seed(session):
    token = Token(chain="bsc", address=TOKEN, creation_timestamp=T0)
    session.add_all([token, Token(chain="bsc", address=QUOTE), Pair(
        chain_id=56, address=PAIR, token0_address=TOKEN, token1_address=QUOTE,
        quote_token=QUOTE, creation_timestamp=T0, creation_block=100, creation_block_hash="creation",
    )])
    session.flush()
    return token


def fact(session, *, tx="one", value=10, at=None, pair=PAIR, side="BUY"):
    row = TradeEvent(chain="bsc", chain_id=56, token_address=TOKEN, pair_address=pair,
                     trader_address=WALLET, tx_hash=tx, block_number=101, block_hash=f"hash-{tx}",
                     timestamp=at or T0 + timedelta(seconds=10), side=side, token_amount=1,
                     quote_amount=value, value_usd=value, price=1, source="fixture")
    session.add(row)
    session.flush()
    return row


def test_same_time_intelligence_refresh_preserves_previous_revision(research_session):
    seed(research_session)
    old_trade = fact(research_session)
    service = IntelligenceAggregationService(research_session, min_wallet_score=0)
    service.refresh(as_of=T0 + timedelta(seconds=20))
    old = research_session.scalar(select(TokenSmartMoneySnapshot))
    old_value = old.smart_money_net_flow
    old_trade.canonical, old_trade.removed = False, True
    fact(research_session, tx="replacement", value=5)

    service.refresh(as_of=T0 + timedelta(seconds=20))

    current = research_session.scalar(select(TokenSmartMoneySnapshot))
    assert current.id != old.id
    assert old.smart_money_net_flow == old_value == 10
    assert current.smart_money_net_flow == 5
    history = research_session.scalars(select(TokenSmartMoneySnapshot).execution_options(
        include_invalid_snapshots=True,
    )).all()
    assert len(history) == 2


def test_published_inputs_and_results_cannot_be_mutated_in_place(research_session):
    seed(research_session)
    fact(research_session)
    IntelligenceAggregationService(research_session, min_wallet_score=0).refresh(as_of=T0 + timedelta(seconds=20))
    row = research_session.scalar(select(TokenSmartMoneySnapshot))
    row.smart_money_net_flow = 999
    with pytest.raises(ValueError, match="immutable"):
        research_session.flush()


def test_superseded_revision_cannot_be_reactivated_in_place(research_session):
    seed(research_session)
    trade = fact(research_session)
    service = IntelligenceAggregationService(research_session, min_wallet_score=0)
    service.refresh(as_of=T0 + timedelta(seconds=20))
    old = research_session.scalar(select(TokenSmartMoneySnapshot))
    trade.canonical, trade.removed = False, True
    fact(research_session, tx="replacement", value=5)
    service.refresh(as_of=T0 + timedelta(seconds=20))
    old.snapshot_status = "VALID"
    with pytest.raises(ValueError, match="immutable"):
        research_session.flush()


def test_same_time_risk_refresh_preserves_previous_inputs(research_session):
    seed(research_session)
    trade = fact(research_session)
    service = RiskAggregationService(research_session)
    service.refresh(as_of=T0 + timedelta(seconds=20))
    old = research_session.scalar(select(TokenRiskSnapshot))
    frozen = deepcopy(old.dimensions_json)
    trade.canonical, trade.removed = False, True
    service.refresh(as_of=T0 + timedelta(seconds=20))
    current = research_session.scalar(select(TokenRiskSnapshot))
    assert current.id != old.id and old.dimensions_json == frozen


def test_unversioned_token_cache_cannot_reintroduce_orphan_risk_facts(research_session):
    token = seed(research_session)
    token.metadata_json = {"is_honeypot": True}
    token.current_liquidity = Decimal(5000)
    RiskAggregationService(research_session).refresh(as_of=T0 + timedelta(seconds=20))
    row = research_session.scalar(select(TokenRiskSnapshot))
    assert row.contract_risk == "UNKNOWN"
    assert row.liquidity_risk == "UNKNOWN"
    assert row.input_manifest["facts"]["token_observation"]["metadata_json"] == {}


def test_orphaned_pair_cannot_supply_early_entry_via_token_cache(research_session):
    seed(research_session)
    p = research_session.scalar(select(Pair))
    p.canonical, p.removed = False, True
    fact(research_session)
    IntelligenceAggregationService(research_session, min_wallet_score=0).refresh(
        as_of=T0 + timedelta(seconds=20),
    )
    snapshot = research_session.scalar(select(TokenSmartMoneySnapshot))
    assert snapshot.early_smart_buy_count is None and snapshot.data_status == "UNKNOWN"


def test_json_orphan_markers_are_excluded_from_intelligence(research_session):
    seed(research_session)
    fact(research_session)
    bad = fact(research_session, tx="orphan", value=100)
    bad.metadata_json = {"orphaned": True}
    IntelligenceAggregationService(research_session, min_wallet_score=0).refresh(
        as_of=T0 + timedelta(seconds=20),
    )
    assert research_session.scalar(select(TokenSmartMoneySnapshot)).smart_money_net_flow == 10


def test_parent_pair_invalidation_excludes_still_flagged_child_trades(research_session):
    seed(research_session)
    fact(research_session)
    p = research_session.scalar(select(Pair))
    p.canonical, p.removed = False, True
    IntelligenceAggregationService(research_session, min_wallet_score=0).refresh_token(
        TOKEN, as_of=T0 + timedelta(seconds=20),
    )
    row = research_session.scalar(select(TokenSmartMoneySnapshot))
    assert row.smart_money_net_flow is None and row.data_status == "UNKNOWN"
    assert row.input_manifest["facts"]["trades"] == []


def test_full_refresh_does_not_leave_a_removed_subjects_previous_result_current(research_session):
    seed(research_session)
    trade = fact(research_session)
    service = IntelligenceAggregationService(research_session, min_wallet_score=0)
    service.refresh(as_of=T0 + timedelta(seconds=20))
    trade.canonical, trade.removed = False, True
    service.refresh(as_of=T0 + timedelta(seconds=20))
    row = research_session.scalar(select(TokenSmartMoneySnapshot))
    assert row.smart_money_net_flow is None and row.data_status == "UNKNOWN"


@pytest.mark.asyncio
async def test_market_aggregate_follows_pair_identity(coverage_session):
    from smms.market.snapshots import materialize_trade_snapshot

    session = coverage_session
    await session.run_sync(seed)
    own = await session.run_sync(fact)
    await session.run_sync(lambda sync: fact(sync, tx="other-pair", value=1000, pair="other-pair"))
    snapshot = await materialize_trade_snapshot(session, own)
    assert snapshot.volume_5m == 10


@pytest.mark.asyncio
async def test_orphan_trigger_cannot_publish_a_known_market_snapshot(coverage_session):
    from smms.market.snapshots import materialize_trade_snapshot

    session = coverage_session
    await session.run_sync(seed)
    own = await session.run_sync(fact)
    own.canonical, own.removed = False, True
    assert await materialize_trade_snapshot(session, own) is None


def test_canonical_invalidation_hides_stale_snapshot_and_alias_latest(research_session):
    from smms.alpha.work import invalidate_coverage
    from smms.db.repositories import TokenSmartMoneySnapshotRepository

    seed(research_session)
    fact(research_session)
    service = IntelligenceAggregationService(research_session, min_wallet_score=0)
    service.refresh(as_of=T0 + timedelta(seconds=30))
    old = research_session.scalar(select(TokenSmartMoneySnapshot))
    invalidate_coverage(research_session, from_block=101, now=T0 + timedelta(seconds=40))
    assert research_session.scalar(select(TokenSmartMoneySnapshot)) is None
    service.refresh(as_of=T0 + timedelta(seconds=20))
    rows = TokenSmartMoneySnapshotRepository(research_session).list_latest_for_tokens(
        {TOKEN}, chain="bsc", score_version="v2", as_of=T0 + timedelta(seconds=50),
    )
    assert len(rows) == 1 and rows[0].id != old.id
    assert utc(rows[0].as_of) == T0 + timedelta(seconds=20)


def test_stale_publication_cannot_acquire_a_new_epoch(research_session):
    from smms.alpha.work import invalidate_coverage
    from smms.research.publication import publish_snapshot

    seed(research_session)
    fact(research_session)
    IntelligenceAggregationService(research_session, min_wallet_score=0).refresh(as_of=T0 + timedelta(seconds=20))
    old = research_session.scalar(select(TokenSmartMoneySnapshot))
    values = {c.name: getattr(old, c.name) for c in old.__table__.columns if c.name != "id"}
    values["as_of"] = T0 + timedelta(seconds=30)
    record = TokenSmartMoneySnapshot(**values)
    invalidate_coverage(research_session, from_block=101, now=T0 + timedelta(seconds=40))
    publish_snapshot(research_session, record)
    assert research_session.scalar(select(TokenSmartMoneySnapshot)) is None


def test_durable_rebuild_recovers_after_a_removed_wallets_last_trade(research_session):
    from smms.alpha.work import invalidate_coverage
    from smms.db.models import WalletSmartMoneySnapshot
    from smms.research.rebuild import rebuild_due

    seed(research_session)
    trade = fact(research_session)
    cutoff = T0 + timedelta(seconds=20)
    IntelligenceAggregationService(research_session, min_wallet_score=0).refresh(as_of=cutoff)
    old = research_session.scalar(select(WalletSmartMoneySnapshot))
    trade.canonical, trade.removed = False, True
    invalidate_coverage(research_session, from_block=101, now=cutoff)
    research_session.commit()
    for _ in range(3):
        rebuild_due(research_session, now=cutoff + timedelta(days=2), batch_size=8)
        research_session.commit()
    current = research_session.scalar(select(WalletSmartMoneySnapshot))
    assert current.id != old.id and current.sample_size == 0
    assert old.sample_size == 1
    token = research_session.scalar(select(TokenSmartMoneySnapshot))
    assert token.smart_money_net_flow is None and token.data_status == "UNKNOWN"


def test_rebuild_cannot_replace_a_fresh_custom_policy_published_during_its_read(research_session, monkeypatch):
    from smms.alpha.work import invalidate_coverage
    from smms.research import rebuild

    seed(research_session)
    fact(research_session)
    cutoff = T0 + timedelta(seconds=20)
    IntelligenceAggregationService(research_session, min_wallet_score=60).refresh(as_of=cutoff)
    invalidate_coverage(research_session, from_block=101, now=cutoff)
    original = rebuild._rebuild

    def interleave(session, old):
        if isinstance(old, TokenSmartMoneySnapshot):
            IntelligenceAggregationService(session, min_wallet_score=0).refresh_token(TOKEN, as_of=cutoff)
        return original(session, old)

    monkeypatch.setattr(rebuild, "_rebuild", interleave)
    rebuild.rebuild_due(research_session, now=cutoff + timedelta(days=1))
    row = research_session.scalar(select(TokenSmartMoneySnapshot))
    assert row.smart_money_net_flow == 10
    assert row.input_manifest["parameters"]["min_wallet_score"] == "0"


def test_epoch_invalidation_rolls_back_with_the_source_transaction(research_session):
    from smms.alpha.work import invalidate_coverage

    seed(research_session)
    fact(research_session)
    IntelligenceAggregationService(research_session).refresh(as_of=T0 + timedelta(seconds=20))
    research_session.commit()
    original_id = research_session.scalar(select(TokenSmartMoneySnapshot.id))
    invalidate_coverage(research_session, from_block=101, now=T0 + timedelta(seconds=30))
    assert research_session.scalar(select(TokenSmartMoneySnapshot)) is None
    research_session.rollback()
    assert research_session.scalar(select(TokenSmartMoneySnapshot.id)) == original_id


def test_rebuild_resumes_in_a_new_database_session(research_session):
    from sqlalchemy.orm import Session

    from smms.alpha.work import invalidate_coverage
    from smms.research.rebuild import rebuild_due

    seed(research_session)
    fact(research_session)
    IntelligenceAggregationService(research_session, min_wallet_score=0).refresh(as_of=T0 + timedelta(seconds=20))
    invalidate_coverage(research_session, from_block=101, now=T0 + timedelta(seconds=30))
    research_session.commit()
    with Session(research_session.bind) as restarted:
        for _ in range(4):
            rebuild_due(restarted, now=T0 + timedelta(days=2), batch_size=1)
            restarted.commit()
        current = restarted.scalar(select(TokenSmartMoneySnapshot))
        assert current is not None and current.smart_money_net_flow == 10
        assert current.research_revision == 1 and current.source_epoch == 2


def test_two_writers_publish_one_revision_with_the_same_source_inputs(research_session):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from sqlalchemy import text
    from sqlalchemy.orm import Session

    if research_session.bind.dialect.name != "postgresql":
        pytest.skip("independent publication writers require PostgreSQL")
    seed(research_session)
    old = fact(research_session)
    IntelligenceAggregationService(research_session, min_wallet_score=0).refresh(as_of=T0 + timedelta(seconds=20))
    old.canonical, old.removed = False, True
    fact(research_session, tx="new", value=5)
    research_session.commit()
    barrier = Barrier(2)

    def writer():
        with Session(research_session.bind) as session:
            session.execute(text("SET LOCAL lock_timeout = '5s'"))
            barrier.wait(timeout=3)
            IntelligenceAggregationService(session, min_wallet_score=0).refresh_token(TOKEN, as_of=T0 + timedelta(seconds=20))
            session.commit()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(writer) for _ in range(2)]
        for future in futures:
            future.result(timeout=10)
    history = research_session.scalars(select(TokenSmartMoneySnapshot).execution_options(include_invalid_snapshots=True)).all()
    assert len(history) == 2
    current = research_session.scalar(select(TokenSmartMoneySnapshot))
    assert current.research_revision == 1 and current.smart_money_net_flow == 5


def test_reorg_can_commit_while_an_old_producer_is_reading(research_session, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from sqlalchemy import text
    from sqlalchemy.orm import Session

    from smms.alpha.work import invalidate_coverage
    from smms.research.state import capture_epoch

    if research_session.bind.dialect.name != "postgresql":
        pytest.skip("independent epoch and producer writers require PostgreSQL")
    seed(research_session)
    trade = fact(research_session)
    research_session.commit()
    captured, release = Event(), Event()
    before = capture_epoch(research_session)
    original = IntelligenceAggregationService._materialize

    def pause(self, **kwargs):
        assert kwargs["source_epoch"] == before
        captured.set()
        assert release.wait(timeout=8)
        return original(self, **kwargs)

    monkeypatch.setattr(IntelligenceAggregationService, "_materialize", pause)

    def producer():
        with Session(research_session.bind) as session:
            with pytest.raises(ValueError, match="epoch"):
                IntelligenceAggregationService(session, min_wallet_score=0).refresh(as_of=T0 + timedelta(seconds=20))
            session.rollback()

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(producer)
        try:
            assert captured.wait(timeout=3)
            research_session.execute(text("SET LOCAL lock_timeout = '3s'"))
            trade.canonical, trade.removed = False, True
            invalidate_coverage(research_session, from_block=101, now=T0 + timedelta(seconds=30))
            research_session.commit()
        finally:
            release.set()
        future.result(timeout=10)
    assert research_session.scalar(select(TokenSmartMoneySnapshot)) is None


def test_rebuild_cursor_recovers_lower_id_that_commits_late(research_session):
    from sqlalchemy.orm import Session

    from smms.db.models import ResearchRebuildJob
    from smms.research.rebuild import rebuild_due
    from smms.research.state import invalidate_derived

    if research_session.bind.dialect.name != "postgresql":
        pytest.skip("sequence allocation before commit requires PostgreSQL")
    seed(research_session)
    fact(research_session)
    IntelligenceAggregationService(research_session).refresh(as_of=T0 + timedelta(seconds=20))
    template = research_session.scalar(select(TokenSmartMoneySnapshot))
    values = {c.name: getattr(template, c.name) for c in template.__table__.columns if c.name != "id"}
    values.update(score_version="unsupported-test", source_epoch=0, research_revision=0, previous_snapshot_id=None)
    invalidate_derived(research_session, reason="TEST", cause={})
    research_session.commit()
    with Session(research_session.bind) as delayed, Session(research_session.bind) as fast:
        early = TokenSmartMoneySnapshot(**values)
        delayed.add(early)
        delayed.flush()
        fast.add(TokenSmartMoneySnapshot(**{**values, "token_address": "second"}))
        fast.commit()
        rebuild_due(research_session, now=T0 + timedelta(days=1), batch_size=4)
        research_session.commit()
        delayed.commit()
        for _ in range(4):
            rebuild_due(research_session, now=T0 + timedelta(days=1), batch_size=4)
            research_session.commit()
        job = research_session.scalar(select(ResearchRebuildJob).where(
            ResearchRebuildJob.kind == "token_smart_money_snapshots", ResearchRebuildJob.snapshot_id == early.id,
        ))
        assert job is not None and job.status == "UNSUPPORTED" and job.last_error == "UNSUPPORTED_VERSION"


def test_empty_market_rebuild_keeps_the_epoch_of_its_input_read(research_session, monkeypatch):
    from smms.alpha.work import invalidate_coverage
    from smms.db.models import TokenMarketSnapshot
    from smms.market import snapshots
    from smms.research.rebuild import rebuild_due

    seed(research_session)
    old = fact(research_session)
    cutoff = T0 + timedelta(seconds=20)
    snapshots.rebuild_trade_snapshot(research_session, old, as_of=cutoff)
    old.canonical, old.removed = False, True
    invalidate_coverage(research_session, from_block=101, now=cutoff)
    original = snapshots.rebuild_trade_snapshot

    def interleave(session, trade, **kwargs):
        result = original(session, trade, **kwargs)
        if result is None:
            fact(session, tx="replacement", value=7)
            invalidate_coverage(session, from_block=101, now=cutoff)
        return result

    monkeypatch.setattr(snapshots, "rebuild_trade_snapshot", interleave)
    rebuild_due(research_session, now=cutoff + timedelta(days=1))
    latest = research_session.scalar(select(TokenMarketSnapshot).order_by(TokenMarketSnapshot.id.desc())
                                      .execution_options(include_invalid_snapshots=True))
    assert latest.source_epoch == 2
    for _ in range(3):
        rebuild_due(research_session, now=cutoff + timedelta(days=1))
    current = research_session.scalar(select(TokenMarketSnapshot))
    assert current is not None and current.volume_5m == 7


def test_stale_producer_cannot_supersede_a_corrected_winner(research_session):
    from smms.alpha.work import invalidate_coverage
    from smms.research.publication import publish_snapshot

    seed(research_session)
    fact(research_session)
    service = IntelligenceAggregationService(research_session, min_wallet_score=0)
    cutoff = T0 + timedelta(seconds=20)
    service.refresh(as_of=cutoff)
    old = research_session.scalar(select(TokenSmartMoneySnapshot))
    captured = {c.name: getattr(old, c.name) for c in old.__table__.columns if c.name != "id"}
    invalidate_coverage(research_session, from_block=101, now=cutoff)
    service.refresh_token(TOKEN, as_of=cutoff)
    winner = research_session.scalar(select(TokenSmartMoneySnapshot))
    publish_snapshot(research_session, TokenSmartMoneySnapshot(**captured))
    assert research_session.scalar(select(TokenSmartMoneySnapshot.id)) == winner.id


def test_identity_map_guard_checks_persisted_supersession(research_session):
    from sqlalchemy.orm import Session

    from smms.research.visibility import is_current_snapshot

    seed(research_session)
    fact(research_session)
    cutoff = T0 + timedelta(seconds=20)
    RiskAggregationService(research_session).refresh(as_of=cutoff)
    research_session.commit()
    old = research_session.scalar(select(TokenRiskSnapshot))
    with Session(research_session.bind) as writer:
        changed = writer.scalar(select(TradeEvent))
        changed.quote_amount = 11
        RiskAggregationService(writer).refresh(as_of=cutoff)
        writer.commit()
    assert research_session.scalar(select(TokenRiskSnapshot.id).where(TokenRiskSnapshot.id == old.id)) is None
    assert is_current_snapshot(research_session, old) is False


def test_same_persisted_numeric_inputs_do_not_change_after_reload(research_session):
    seed(research_session)
    held_trade = fact(research_session)
    service = IntelligenceAggregationService(research_session, min_wallet_score=0)
    cutoff = T0 + timedelta(seconds=20)
    service.refresh(as_of=cutoff)
    original = research_session.scalar(select(TokenSmartMoneySnapshot))
    original_id, original_digest = original.id, original.input_manifest["digest"]
    research_session.commit()
    assert held_trade is not None
    service.refresh(as_of=cutoff)
    row = research_session.scalar(select(TokenSmartMoneySnapshot))
    assert row.id == original_id and row.input_manifest["digest"] == original_digest


@pytest.mark.asyncio
async def test_audit_api_retains_hidden_values_and_live_api_returns_unknown(coverage_session, monkeypatch):
    from contextlib import asynccontextmanager

    from smms.alpha.work import invalidate_coverage
    from smms.api import app as api
    from smms.api import research

    session = coverage_session
    await session.run_sync(seed)
    await session.run_sync(fact)
    await session.run_sync(lambda sync: IntelligenceAggregationService(sync, min_wallet_score=0).refresh(
        as_of=T0 + timedelta(seconds=20),
    ))
    old = await session.scalar(select(TokenSmartMoneySnapshot))
    await session.run_sync(lambda sync: invalidate_coverage(sync, from_block=101, now=T0 + timedelta(seconds=30)))
    await session.commit()

    @asynccontextmanager
    async def scope(_settings):
        yield session

    monkeypatch.setattr(api, "session_scope", scope)
    monkeypatch.setattr(research, "session_scope", scope)
    live = await api.token_smart_money(TOKEN, limit=100, as_of=T0 + timedelta(seconds=20), score_version="v2")
    assert live["data_status"] == "UNKNOWN" and live["smart_money"] is None
    audit = await research.snapshot_lineage("token_smart_money_snapshots", old.id)
    assert audit["audit_only"] and audit["usable_current"] is False
    assert audit["snapshot"]["smart_money_net_flow"] == "10"
    epochs = await research.research_epochs(limit=1, before_epoch=None)
    assert len(epochs["epochs"]) == 1 and epochs["epochs"][0]["reason"] == "CANONICAL_INVALIDATION"


@pytest.mark.asyncio
async def test_rebuild_worker_heartbeat_has_its_own_session(monkeypatch):
    import asyncio
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from smms import runtime

    started, released, committed = asyncio.Event(), asyncio.Event(), asyncio.Event()
    beats = []

    class Work:
        async def run_sync(self, _callback):
            started.set()
            await released.wait()
            return 0

        async def commit(self):
            committed.set()

    @asynccontextmanager
    async def scope(_settings):
        yield Work()

    async def heartbeat(worker, status, **_kwargs):
        if started.is_set():
            beats.append((worker, status))
            released.set()

    monkeypatch.setattr(runtime, "get_settings", lambda: SimpleNamespace(runtime_heartbeat_interval_seconds=0.01))
    monkeypatch.setattr(runtime, "session_scope", scope)
    monkeypatch.setattr(runtime, "heartbeat", heartbeat)
    task = asyncio.create_task(runtime.run_research_rebuild())
    try:
        await asyncio.wait_for(committed.wait(), timeout=1)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert beats and beats[0] == ("research-rebuild", "RUNNING")

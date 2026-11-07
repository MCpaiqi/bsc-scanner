"""Legacy research endpoints must share canonical, point-in-time source rules."""

import os
from contextlib import asynccontextmanager
from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update
from test_alpha_collector import coverage_session as _coverage_session
from test_derived_snapshot_integrity import PAIR, T0, TOKEN, WALLET, fact, seed

from smms.api import app as api
from smms.db.models import MarketEvent, Pair, ResearchState, TradeEvent, WalletSmartMoneySnapshot
from smms.research.reads import CanonicalResearchRead

coverage_session = _coverage_session
CUTOFF = T0 + timedelta(seconds=20)


@pytest.fixture
def api_session(coverage_session, monkeypatch):
    @asynccontextmanager
    async def scope(_settings):
        yield coverage_session

    monkeypatch.setattr(api, "session_scope", scope)
    return coverage_session


def cohort(**extra):
    return {"chain": "bsc", "as_of": CUTOFF.isoformat(), "successful_tokens": [
        {"address": TOKEN, "first_seen_at": T0.isoformat(), "ath_multiple": "2"},
    ], **extra}


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["removed", "json_orphan", "parent_removed", "future_parent"])
async def test_replay_excludes_invalid_sources_before_limiting(api_session, defect):
    session = api_session
    await session.run_sync(seed)
    bad = await session.run_sync(lambda sync: fact(sync, tx="bad", at=T0 + timedelta(seconds=1)))
    if defect == "removed":
        bad.canonical, bad.removed = False, True
    elif defect == "json_orphan":
        bad.metadata_json = {"confirmation_status": "orphaned"}
    else:
        parent = await session.scalar(select(Pair).where(Pair.address == PAIR))
        if defect == "parent_removed":
            parent.canonical, parent.removed = False, True
        else:
            parent.creation_timestamp = T0 + timedelta(seconds=5)
    good = await session.run_sync(lambda sync: fact(sync, tx="good", pair="0x" + "e" * 40))
    good.trader_address = "0x" + "f" * 40
    await session.commit()

    result = await api.wallet_replay(cohort(max_trades=1))

    assert result["successful_tokens"][0]["wallets"] == [good.trader_address]
    assert result["truncated"] is False
    assert result["cohort_status"] == "USER_SUPPLIED_UNVERIFIED"
    provenance = result["research_provenance"]
    assert provenance["parameters"]["as_of"] == CUTOFF.isoformat(timespec="microseconds")
    assert [row["tx_hash"] for row in provenance["facts"]["trades"]] == ["good"]


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["graph", "clusters"])
async def test_orphan_wallet_activity_cannot_discover_a_neighborhood(api_session, endpoint):
    await api_session.run_sync(seed)
    bad = await api_session.run_sync(fact)
    bad.metadata_json = {"removed": True}
    other = await api_session.run_sync(lambda sync: fact(sync, tx="neighbor"))
    other.trader_address = "0x" + "f" * 40
    await api_session.commit()

    result = await getattr(api, f"wallet_{endpoint}")(WALLET, chain="bsc", as_of=CUTOFF)

    assert result["data_status"] == "UNKNOWN"
    assert result.get("nodes", result.get("clusters")) == []
    assert result["research_provenance"]["facts"]["trades"] == []


@pytest.mark.asyncio
async def test_replay_rejects_epoch_change_during_calculation(api_session, monkeypatch):
    await api_session.run_sync(seed)
    await api_session.run_sync(fact)
    await api_session.commit()
    original = api.replay_historical_smart_money

    def reconcile(*args, **kwargs):
        result = original(*args, **kwargs)
        api_session.add(ResearchState(chain="bsc", epoch=2))
        return result

    monkeypatch.setattr(api, "replay_historical_smart_money", reconcile)
    with pytest.raises(HTTPException) as caught:
        await api.wallet_replay(cohort())
    assert caught.value.status_code == 409
    assert caught.value.detail["data_status"] == "REBUILDING"


@pytest.mark.asyncio
async def test_flow_keeps_missing_usd_unknown_and_tracks_wallet_source(api_session):
    await api_session.run_sync(seed)
    await api_session.run_sync(fact)
    sell = await api_session.run_sync(lambda sync: fact(sync, tx="sell", side="SELL"))
    sell.value_usd = None
    snapshot = WalletSmartMoneySnapshot(chain="bsc", wallet_address=WALLET, as_of=T0,
                                        score=80, score_version="v2", profile_version="v1",
                                        confidence="LOW", confidence_score=0.1, sample_size=1,
                                        data_completeness=1, historical_coverage=1, trade_quality=1)
    api_session.add(snapshot)
    await api_session.commit()

    result = await api.token_smart_money_flow(TOKEN, window="1m", chain="bsc", as_of=CUTOFF)

    assert result["inflow"] == Decimal(10)
    assert result["outflow"] is None and result["net_flow"] is None
    assert result["accumulation"] is None and result["distribution"] is None
    assert result["data_status"] == "DEGRADED"
    assert result["unvalued_trade_count"] == 1
    assert result["research_provenance"]["facts"]["wallet_snapshots"][0]["id"] == snapshot.id


@pytest.mark.asyncio
async def test_unknown_wallet_classification_cannot_certify_zero_flow(api_session):
    await api_session.run_sync(seed)
    await api_session.run_sync(fact)
    await api_session.commit()
    result = await api.token_smart_money_flow(TOKEN, window="1m", chain="bsc", as_of=CUTOFF)
    assert result["net_flow"] is None
    assert result["classification_status"] == "UNKNOWN"
    assert result["unclassified_wallet_count"] == 1


def wallet_snapshot(**extra):
    return WalletSmartMoneySnapshot(chain="bsc", wallet_address=WALLET, as_of=T0,
                                   score=80, score_version="v2", profile_version="v1",
                                   confidence="LOW", confidence_score=0.1, sample_size=1,
                                   data_completeness=1, historical_coverage=1, trade_quality=1, **extra)


@pytest.mark.asyncio
async def test_same_epoch_dependency_withdrawal_rejects_flow(api_session, monkeypatch):
    await api_session.run_sync(seed)
    await api_session.run_sync(fact)
    snapshot = wallet_snapshot()
    api_session.add(snapshot)
    await api_session.commit()
    original = api.smart_money_flow

    def withdraw(*args, **kwargs):
        result = original(*args, **kwargs)
        snapshot.snapshot_status = "INVALIDATED"
        return result

    monkeypatch.setattr(api, "smart_money_flow", withdraw)
    with pytest.raises(HTTPException) as caught:
        await api.token_smart_money_flow(TOKEN, window="1m", chain="bsc", as_of=CUTOFF)
    assert caught.value.status_code == 409


@pytest.mark.asyncio
async def test_reader_refreshes_cached_inputs_and_keeps_a_stable_manifest(api_session):
    await api_session.run_sync(seed)
    row = await api_session.run_sync(fact)
    await api_session.commit()
    assert row.value_usd == 10
    await api_session.execute(update(TradeEvent).where(TradeEvent.id == row.id).values(value_usd=7)
                              .execution_options(synchronize_session=False))
    await api_session.commit()
    assert row.value_usd == 10
    first = await api.wallet_replay(cohort())
    second = await api.wallet_replay(cohort())
    assert first["research_provenance"] == second["research_provenance"]
    assert first["research_provenance"]["facts"]["trades"][0]["value_usd"] == "7"
    assert (await api_session.scalars(select(ResearchState))).all() == []


@pytest.mark.asyncio
async def test_graph_limit_filters_before_neighborhood_discovery(api_session):
    await api_session.run_sync(seed)
    bad = await api_session.run_sync(lambda sync: fact(sync, tx="orphan", at=T0))
    bad.canonical = False
    good = await api_session.run_sync(fact)
    await api_session.commit()

    def run(sync):
        reader = CanonicalResearchRead(sync, chain="bsc", as_of=CUTOFF, parameters={})
        rows, truncated = reader.neighborhood(WALLET, limit=1)
        return [row.id for row in rows], truncated, reader.finish()

    rows, truncated, provenance = await api_session.run_sync(run)
    assert rows == [good.id] and truncated is False
    assert [row["id"] for row in provenance["facts"]["trades"]] == [good.id]


@pytest.mark.asyncio
async def test_market_events_excludes_orphan_and_future_sources_before_limit(api_session):
    await api_session.run_sync(seed)
    for tx, seconds, metadata in [("valid", 10, {}), ("orphan", 12, {"orphaned": True}),
                                   ("future", 30, {})]:
        api_session.add(MarketEvent(chain_id=56, event_type="swap", token_address=TOKEN,
                                   pair_address=PAIR, tx_hash=tx, timestamp=T0 + timedelta(seconds=seconds),
                                   block_number=101, source="fixture", metadata_json=metadata))
    await api_session.commit()
    result = await api.bsc_market_events(limit=1, token=TOKEN, as_of=CUTOFF)
    assert [row["tx_hash"] for row in result["events"]] == ["valid"]
    assert result["truncated"] is False


@pytest.mark.asyncio
async def test_profile_tracks_both_snapshot_and_network_at_one_cutoff(api_session):
    await api_session.run_sync(seed)
    await api_session.run_sync(fact)
    api_session.add(wallet_snapshot())
    await api_session.commit()
    result = await api.wallet_profile(WALLET, chain="bsc", as_of=CUTOFF, score_version="v2")
    assert result["network_as_of"] == CUTOFF
    assert result["research_provenance"]["facts"]["wallet_snapshots"][0]["wallet_address"] == WALLET


@pytest.mark.asyncio
async def test_empty_cohort_remains_empty_without_reading_unrelated_facts(api_session):
    await api_session.run_sync(seed)
    await api_session.run_sync(fact)
    await api_session.commit()
    result = await api.wallet_replay(cohort(successful_tokens=[]))
    assert result["successful_tokens"] == []
    assert result["research_provenance"]["facts"]["trades"] == []


@pytest.mark.asyncio
async def test_truncated_flow_cannot_report_an_exact_net_or_direction(api_session, monkeypatch):
    await api_session.run_sync(seed)
    await api_session.run_sync(fact)
    await api_session.run_sync(lambda sync: fact(sync, tx="later", side="SELL", value=100))
    api_session.add(wallet_snapshot())
    await api_session.commit()
    original = CanonicalResearchRead.trades

    def limited(self, *conditions, **kwargs):
        return original(self, *conditions, **{**kwargs, "limit": 1})

    monkeypatch.setattr(CanonicalResearchRead, "trades", limited)
    result = await api.token_smart_money_flow(TOKEN, window="1m", chain="bsc", as_of=CUTOFF)
    assert result["truncated"] is True
    assert result["net_flow"] is None and result["accumulation"] is None
    assert result["inflow"] is None and result["outflow"] is None
    assert result["known_inflow"] == 10 and result["data_status"] == "DEGRADED"


@pytest.mark.asyncio
async def test_latest_visible_wallet_classification_respects_cutoff(api_session):
    await api_session.run_sync(seed)
    await api_session.run_sync(fact)
    old = wallet_snapshot()
    future = wallet_snapshot()
    future.as_of, future.score = CUTOFF + timedelta(seconds=1), 0
    hidden = wallet_snapshot(research_revision=1, snapshot_status="SUPERSEDED")
    hidden.score = 0
    api_session.add_all([old, future, hidden])
    await api_session.commit()
    result = await api.token_smart_money_flow(TOKEN, window="1m", chain="bsc", as_of=CUTOFF)
    assert result["net_flow"] == 10
    assert [row["id"] for row in result["research_provenance"]["facts"]["wallet_snapshots"]] == [old.id]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["epoch", "dependency"])
async def test_postgres_concurrent_reconciliation_is_not_returned_as_current(api_session, monkeypatch, change):
    if api_session.bind.dialect.name != "postgresql":
        pytest.skip("requires two independently committed PostgreSQL connections")
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from smms.research.state import invalidate_derived

    await api_session.run_sync(seed)
    await api_session.run_sync(fact)
    snapshot = wallet_snapshot()
    api_session.add(snapshot)
    await api_session.commit()
    schema_options = api_session.bind.get_execution_options()["schema_translate_map"]
    engine = create_engine(os.environ["SMMS_RESEARCH_TEST_DATABASE_URL"],
                           connect_args={"options": "-c statement_timeout=5000"})
    original = api.smart_money_flow

    def reconcile(*args, **kwargs):
        result = original(*args, **kwargs)
        with Session(engine.execution_options(schema_translate_map=schema_options)) as writer:
            if change == "epoch":
                invalidate_derived(writer, reason="TEST_CONCURRENT_REORG", cause={})
            else:
                row = writer.scalar(select(WalletSmartMoneySnapshot).where(
                    WalletSmartMoneySnapshot.id == snapshot.id))
                row.snapshot_status = "INVALIDATED"
            writer.commit()
        return result

    monkeypatch.setattr(api, "smart_money_flow", reconcile)
    try:
        with pytest.raises(HTTPException) as caught:
            await api.token_smart_money_flow(TOKEN, window="1m", chain="bsc", as_of=CUTOFF)
        assert caught.value.status_code == 409
        assert caught.value.detail["data_status"] == "REBUILDING"
    finally:
        await api_session.rollback()
        engine.dispose()


@pytest.mark.asyncio
async def test_http_responses_serialize_provenance_and_unknown_flow(api_session):
    await api_session.run_sync(seed)
    await api_session.run_sync(fact)
    await api_session.commit()
    async with AsyncClient(transport=ASGITransport(app=api.app), base_url="http://test") as client:
        replay = await client.post("/api/wallet/replay", json=cohort())
        assert replay.status_code == 200
        assert replay.json()["research_provenance"]["facts"]["trades"][0]["value_usd"] == "10"
        flow = await client.get(f"/api/token/{TOKEN}/smart-money-flow",
                                params={"window": "1m", "as_of": CUTOFF.isoformat()})
        assert flow.status_code == 200
        assert flow.json()["net_flow"] is None
        assert flow.json()["classification_status"] == "UNKNOWN"


@pytest.mark.asyncio
async def test_graph_cluster_cap_changes_provenance_and_discloses_clipping(api_session, monkeypatch):
    await api_session.run_sync(seed)
    tokens = [TOKEN, "0x" + "e" * 40]
    for index, token in enumerate(tokens):
        own = await api_session.run_sync(lambda sync, i=index: fact(sync, tx=f"own-{i}", side="SELL"))
        own.token_address = token
        for member in range(2):
            row = await api_session.run_sync(lambda sync, i=index, n=member: fact(sync, tx=f"buy-{i}-{n}"))
            row.token_address = token
            row.trader_address = f"0x{1000 + index * 10 + member:040x}"
    await api_session.commit()
    settings = api.get_settings().model_copy(update={"graph_max_cluster_nodes": 500})
    monkeypatch.setattr(api, "get_settings", lambda: settings)
    full = await api.wallet_graph(WALLET, chain="bsc", as_of=CUTOFF)
    assert sum(node["type"] == "cluster" for node in full["nodes"]) == 2
    settings = settings.model_copy(update={"graph_max_cluster_nodes": 1})
    clipped = await api.wallet_graph(WALLET, chain="bsc", as_of=CUTOFF)
    assert sum(node["type"] == "cluster" for node in clipped["nodes"]) == 1
    assert clipped["truncated"] is True
    assert "cluster_node_limit" in clipped["truncation_reasons"]
    assert clipped["research_provenance"]["digest"] != full["research_provenance"]["digest"]

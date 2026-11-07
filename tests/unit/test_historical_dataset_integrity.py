"""Extraction-to-evaluation regressions and immutable dataset recovery."""

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from test_alpha_collector import coverage_session as _coverage_session
from test_derived_snapshot_integrity import PAIR, T0, TOKEN, fact, seed
from test_research_integrity import research_session as _research_session

from smms.backtest.dataset import HistoricalDatasetBuilder, load_materialized_dataset
from smms.backtest.signal_engine import run_backtest
from smms.db.models import BacktestDataset, BacktestDatasetRow, Pair, TokenScore
from smms.research.rebuild import rebuild_due
from smms.research.state import invalidate_derived

research_session = _research_session
coverage_session = _coverage_session


def price(session, *, at=T0, value=2, tx="entry", pair=PAIR):
    row = fact(session, tx=tx, at=at, pair=pair)
    row.price, row.price_usd = 99, value
    session.flush()
    return row


def build(session, start=T0, end=T0):
    return [r for r in HistoricalDatasetBuilder(session).build(start, end)[0] if r.token == TOKEN]


def materialize(session, label="history", **kwargs):
    return HistoricalDatasetBuilder(session).materialize(T0, T0, dataset_version=label, **kwargs)[0]


def test_extraction_uses_explicit_usd_and_freezes_entry_pair(research_session):
    seed(research_session)
    price(research_session)
    research_session.add(Pair(chain_id=56, address="other", token0_address=TOKEN,
                              token1_address="quote", creation_timestamp=T0))
    price(research_session, at=T0 + timedelta(seconds=30), value=4, tx="endpoint")
    price(research_session, at=T0 + timedelta(seconds=30), value=200, tx="other", pair="other")
    rows = build(research_session)
    result = run_backtest([row.as_dict() for row in rows], strategy="universe")
    assert result.signals[0].entry_price == 2
    evaluation = next(e for e in result.evaluations if e.horizon == "30s")
    assert evaluation.return_pct == 1
    assert rows[0].model_version == "dataset-v5"
    assert all(p["provenance"]["pair_address"] == PAIR for p in rows[0].evaluation_price_path)


def test_unlabelled_quote_preserves_universe_but_has_no_usd_entry(research_session):
    seed(research_session)
    row = price(research_session)
    row.price_usd, row.value_usd = None, None
    rows = build(research_session)
    assert len(rows) == 1 and rows[0].token == TOKEN
    assert not rows[0].data_quality["price"] and rows[0].feature_price_history == ()


def test_last_horizon_includes_the_shared_endpoint_tolerance(research_session):
    seed(research_session)
    price(research_session)
    price(research_session, at=T0 + timedelta(hours=24, seconds=3), value=3, tx="24h")
    result = run_backtest([build(research_session)[0].as_dict()], strategy="universe")
    evaluation = next(e for e in result.evaluations if e.horizon == "24h")
    assert evaluation.available and evaluation.return_pct == Decimal("0.5")


def test_stale_entry_price_cannot_be_reused_by_historical_row(research_session):
    seed(research_session)
    price(research_session)
    rows = build(research_session, T0 + timedelta(seconds=31), T0 + timedelta(seconds=31))
    assert rows[0].feature_price_history == () and not rows[0].data_quality["price"]


def test_unversioned_token_score_is_not_a_historical_feature(research_session):
    token = seed(research_session)
    price(research_session)
    research_session.add(TokenScore(token_id=token.id, timestamp=T0, version="v2", market_score=99,
                                   volume_score=99))
    research_session.flush()
    assert build(research_session)[0].market["score"] == 100


def test_dataset_and_rows_are_hidden_after_canonical_invalidation(research_session):
    seed(research_session)
    price(research_session)
    dataset = materialize(research_session)
    assert load_materialized_dataset(research_session, dataset.id)
    invalidate_derived(research_session, reason="TEST_REORG", cause={})
    research_session.commit()
    assert research_session.scalar(select(BacktestDataset)) is None
    assert research_session.scalar(select(BacktestDatasetRow)) is None
    with pytest.raises(LookupError):
        load_materialized_dataset(research_session, dataset.id)
    audit = research_session.scalar(select(BacktestDatasetRow).execution_options(include_invalid_snapshots=True))
    assert audit.payload_json["market"]["price"] == "2"


def test_rebuild_appends_dataset_revision_with_original_dates(research_session):
    seed(research_session)
    old_fact = price(research_session)
    old = materialize(research_session)
    old_fact.canonical, old_fact.removed = False, True
    price(research_session, value=3, tx="replacement")
    invalidate_derived(research_session, reason="TEST_REORG", cause={})
    research_session.commit()
    rebuild_due(research_session, now=T0 + timedelta(days=2))
    research_session.commit()
    current = research_session.scalar(select(BacktestDataset))
    assert current is not None and current.id != old.id and current.previous_snapshot_id == old.id
    assert current.period_from.replace(tzinfo=T0.tzinfo) == T0
    assert current.period_to.replace(tzinfo=T0.tzinfo) == T0
    assert load_materialized_dataset(research_session, "history")[0]["market"]["price"] == "3"
    with pytest.raises(LookupError):
        load_materialized_dataset(research_session, old.id)


def test_unchanged_materialization_is_idempotent(research_session):
    seed(research_session)
    price(research_session)
    old = materialize(research_session)
    current = materialize(research_session)
    assert old.id == current.id
    assert len(research_session.scalars(select(BacktestDatasetRow).where(BacktestDatasetRow.token == TOKEN)).all()) == 1


def test_dataset_rows_cannot_be_overwritten(research_session):
    seed(research_session)
    price(research_session)
    materialize(research_session)
    row = research_session.scalar(select(BacktestDatasetRow))
    row.payload_json = {"fake": True}
    with pytest.raises(ValueError, match="immutable"):
        research_session.flush()


def test_stream_cannot_complete_after_epoch_changes(research_session):
    seed(research_session)
    price(research_session)
    stream, _ = HistoricalDatasetBuilder(research_session).stream_build(T0, T0)
    invalidate_derived(research_session, reason="TEST_REORG", cause={})
    with pytest.raises(ValueError, match="epoch"):
        list(stream)


def test_materialize_without_commit_rolls_back_all_artifacts(research_session):
    seed(research_session)
    price(research_session)
    research_session.commit()
    materialize(research_session, commit=False)
    research_session.rollback()
    assert research_session.scalar(select(BacktestDataset)) is None


def test_same_epoch_source_revision_invalidates_dataset_and_waits_for_rebuild(research_session):
    from smms.db.models import TokenRiskSnapshot
    from smms.research.lineage import manifest
    from smms.research.publication import publish_snapshot

    seed(research_session)
    price(research_session)

    def risk(level):
        return publish_snapshot(research_session, TokenRiskSnapshot(
            chain="bsc", token_address=TOKEN, as_of=T0, risk_version="risk-v1",
            overall_risk=level, contract_risk=level, liquidity_risk=level, holder_risk=level,
            behavior_risk=level, confidence="HIGH", source_epoch=0,
            input_manifest=manifest(kind="token_risk_snapshots", parameters={}, facts={"level": level}),
        ))

    risk("LOW")
    old = materialize(research_session)
    risk("CRITICAL")
    research_session.commit()
    assert research_session.scalar(select(BacktestDataset)) is None
    rebuild_due(research_session, now=T0 + timedelta(days=2))
    research_session.commit()
    current = research_session.scalar(select(BacktestDataset))
    assert current.previous_snapshot_id == old.id
    assert load_materialized_dataset(research_session, "history")[0]["risk"]["overall_risk"] == "CRITICAL"


def test_future_snapshot_asof_cannot_supply_a_past_entry(research_session):
    from smms.db.models import TokenMarketSnapshot

    seed(research_session)
    price(research_session)
    tick = price(research_session, at=T0 + timedelta(seconds=1), tx="tick")
    tick.price_usd, tick.value_usd = None, None
    research_session.add(TokenMarketSnapshot(
        chain="bsc", chain_id=56, token_address=TOKEN, pair_address=PAIR,
        source="fixture", status="KNOWN", price_usd=999,
        observed_at=T0 + timedelta(seconds=1), as_of=T0 + timedelta(seconds=10),
    ))
    rows = build(research_session, T0, T0 + timedelta(seconds=1))
    assert rows[-1].market["price"] == "2"


def test_historical_and_forward_public_paths_use_the_same_price_path(research_session):
    from smms.db.models import LiveSignalOutcome, MarketEvent
    from smms.radar.engine import RadarEngine
    from smms.radar.live_forward import evaluate_due, record_observation

    seed(research_session)
    price(research_session)
    row = RadarEngine(research_session).ingest(research_session.scalar(select(Pair)), at=T0)[0]
    observation = record_observation(research_session, row, "WATCH", at=T0)
    price(research_session, at=T0 + timedelta(seconds=10), value=8, tx="peak")
    price(research_session, at=T0 + timedelta(seconds=20), value=1, tx="trough")
    price(research_session, at=T0 + timedelta(seconds=30), value=4, tx="endpoint")
    research_session.add(MarketEvent(
        chain_id=56, token_address=TOKEN, pair_address=PAIR, event_type="SWAP", tx_hash="lower-priority",
        timestamp=T0 + timedelta(seconds=30), price=1000, metadata_json={"price_basis": "USD"}, source="fixture",
    ))
    evaluate_due(research_session, at=T0 + timedelta(seconds=35))
    forward = research_session.scalar(select(LiveSignalOutcome).where(
        LiveSignalOutcome.observation_id == observation.id, LiveSignalOutcome.horizon == "30s",
    ))
    historical = run_backtest([build(research_session)[0].as_dict()], strategy="universe")
    evaluated = next(e for e in historical.evaluations if e.horizon == "30s")
    assert observation.price == historical.signals[0].entry_price == 2
    assert evaluated.return_pct == forward.return_pct == 1
    assert evaluated.max_drawdown == forward.max_drawdown_pct == Decimal("-0.875")
    assert evaluated.high_return == forward.max_return_pct == 3


def test_unproven_launchpad_cache_is_not_discovery_evidence(research_session):
    from smms.db.models import Token

    research_session.add(Token(chain="bsc", address=TOKEN, launchpad="flap", first_seen_at=T0))
    assert build(research_session) == []


@pytest.mark.asyncio
async def test_api_rejects_stale_datasets_but_exposes_paged_audit(coverage_session, monkeypatch):
    from contextlib import asynccontextmanager

    from httpx import ASGITransport, AsyncClient

    from smms.api import app as api
    from smms.api import research

    session = coverage_session
    await session.run_sync(seed)
    await session.run_sync(price)
    old = await session.run_sync(materialize)

    @asynccontextmanager
    async def scope(_settings):
        yield session

    monkeypatch.setattr(api, "session_scope", scope)
    monkeypatch.setattr(research, "session_scope", scope)
    async with AsyncClient(transport=ASGITransport(app=api.app), base_url="http://test") as client:
        response = await client.get(f"/api/backtest/datasets/{old.id}/summary")
        assert response.status_code == 200 and response.json()["input_digest"]
        await session.run_sync(lambda sync: invalidate_derived(sync, reason="TEST_REORG", cause={}))
        await session.commit()
        for route in ("/api/backtest/summary", "/api/backtest/signals",
                      f"/api/backtest/datasets/{old.id}/summary", f"/api/backtest/datasets/{old.id}/signals"):
            response = await client.get(route)
            assert response.status_code == 409 and response.json()["detail"]["status"] == "REBUILDING"
        response = await client.get(f"/api/research/datasets/{old.id}/rows?limit=1")
        assert response.status_code == 200
        payload = response.json()
        assert payload["audit_only"] and len(payload["rows"]) == 1 and payload["next_cursor"]
        lineage = (await client.get(f"/api/research/snapshots/backtest_datasets/{old.id}")).json()
        assert lineage["usable_current"] is False
        assert (await client.get("/api/backtest/datasets/999999/summary")).status_code == 404


@pytest.mark.parametrize("receipt_seconds, evaluation_seconds, available", [(31, 35, True), (40, 35, False), (40, 60, True)])
def test_price_receipt_cutoff_is_explicit_and_matches_forward(research_session, receipt_seconds, evaluation_seconds, available):
    from smms.db.models import LiveSignalOutcome, TokenMarketSnapshot
    from smms.radar.engine import RadarEngine
    from smms.radar.live_forward import evaluate_due, record_observation

    seed(research_session)
    price(research_session)
    candidate = RadarEngine(research_session).ingest(research_session.scalar(select(Pair)), at=T0)[0]
    observation = record_observation(research_session, candidate, "WATCH", at=T0)
    research_session.add(TokenMarketSnapshot(
        chain="bsc", chain_id=56, token_address=TOKEN, pair_address=PAIR, source="fixture", status="KNOWN",
        observed_at=T0 + timedelta(seconds=30), as_of=T0 + timedelta(seconds=receipt_seconds), price_usd=4,
    ))
    cutoff = T0 + timedelta(seconds=evaluation_seconds)
    evaluate_due(research_session, at=cutoff)
    forward = research_session.scalar(select(LiveSignalOutcome).where(
        LiveSignalOutcome.observation_id == observation.id, LiveSignalOutcome.horizon == "30s",
    ))
    rows = HistoricalDatasetBuilder(research_session, evaluation_as_of=cutoff).build(T0, T0)[0]
    payload = next(r.as_dict() for r in rows if r.token == TOKEN)
    result = run_backtest([payload], strategy="universe")
    historical = next(e for e in result.evaluations if e.horizon == "30s")
    assert payload["evaluation_as_of"] == cutoff.isoformat()
    assert historical.available is available
    assert historical.return_pct == (forward.return_pct if forward is not None else None)


def test_loader_refreshes_nested_payload_cache_from_persisted_rows(research_session):
    seed(research_session)
    price(research_session)
    dataset = materialize(research_session)
    row = research_session.scalar(select(BacktestDatasetRow).where(BacktestDatasetRow.token == TOKEN))
    row.payload_json["market"]["price"] = "999"
    assert load_materialized_dataset(research_session, dataset.id)[0]["market"]["price"] == "2"
    loaded = load_materialized_dataset(research_session, dataset.id)
    loaded[0]["market"]["price"] = "888"
    assert row.payload_json["market"]["price"] == "2"


def test_dataset_rebuild_preserves_evaluation_availability_cutoff(research_session):
    seed(research_session)
    price(research_session)
    cutoff = T0 + timedelta(seconds=35)
    old, _ = HistoricalDatasetBuilder(research_session, evaluation_as_of=cutoff).materialize(T0, T0, dataset_version="cutoff-study")
    invalidate_derived(research_session, reason="TEST_REORG", cause={})
    research_session.commit()
    rebuild_due(research_session, now=T0 + timedelta(days=2))
    research_session.commit()
    current = research_session.scalar(select(BacktestDataset))
    assert current.previous_snapshot_id == old.id
    assert current.input_manifest["parameters"]["evaluation_as_of"] == cutoff.isoformat(timespec="microseconds")
    assert load_materialized_dataset(research_session, "cutoff-study")[0]["evaluation_as_of"] == cutoff.isoformat()


def test_legacy_recipe_name_cannot_mislabel_current_extraction(research_session):
    with pytest.raises(ValueError, match="unsupported"):
        HistoricalDatasetBuilder(research_session, model_version="dataset-v2").build(T0, T0)


def test_postgres_dataset_writers_converge_without_duplicate_payloads(research_session):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from sqlalchemy.orm import Session

    if research_session.bind.dialect.name != "postgresql":
        pytest.skip("concurrent publication requires PostgreSQL")
    seed(research_session)
    price(research_session)
    research_session.commit()
    ready = Barrier(2)

    def writer():
        with Session(research_session.bind) as session:
            ready.wait(timeout=5)
            return materialize(session).id

    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(writer) for _ in range(2)]
        results = [job.result(timeout=20) for job in jobs]
    assert results[0] == results[1]
    assert len(research_session.scalars(select(BacktestDatasetRow)).all()) == 2

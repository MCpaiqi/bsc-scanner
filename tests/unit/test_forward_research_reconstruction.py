"""Immutable Forward inputs/results and durable canonical recovery."""

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from test_alpha_collector import coverage_session as _coverage_session
from test_derived_snapshot_integrity import PAIR, T0, seed
from test_historical_dataset_integrity import price
from test_research_integrity import research_session as _research_session

from smms.db.models import LiveSignalObservation, LiveSignalOutcome, Pair
from smms.evaluation import utc
from smms.radar.engine import RadarEngine
from smms.radar.live_forward import evaluate_due, performance, record_observation
from smms.research.rebuild import rebuild_due
from smms.research.state import invalidate_derived

research_session = _research_session
coverage_session = _coverage_session
EVALUATED = T0 + timedelta(seconds=35)


def observe(session):
    seed(session)
    price(session)
    candidate = RadarEngine(session).ingest(session.scalar(select(Pair).where(Pair.address == PAIR)), at=T0)[0]
    return candidate, record_observation(session, candidate, "WATCH", at=T0)


def complete(session):
    candidate, observation = observe(session)
    endpoint = price(session, at=T0 + timedelta(seconds=30), value=4, tx="endpoint")
    evaluate_due(session, at=EVALUATED)
    result = session.scalar(select(LiveSignalOutcome).where(LiveSignalOutcome.horizon == "30s"))
    session.commit()
    return candidate, observation, endpoint, result


def recover(session):
    for step in range(8):
        rebuild_due(session, now=T0 + timedelta(days=2, minutes=step))
        session.commit()


def test_reorg_immediately_hides_old_forward_inputs_and_returns(research_session):
    _, _, _, old = complete(research_session)
    assert old.return_pct == 1
    invalidate_derived(research_session, reason="TEST_REORG", cause={})
    research_session.commit()
    assert research_session.scalar(select(LiveSignalObservation)) is None
    assert research_session.scalar(select(LiveSignalOutcome)) is None
    assert performance(research_session)["horizons"]["30s"]["WATCH"]["sample_count"] == 0
    assert performance(research_session)["data_status"] == "REBUILDING"


def test_rebuild_corrects_price_path_and_preserves_original_evaluation_time(research_session):
    _, original, endpoint, old = complete(research_session)
    endpoint.canonical, endpoint.removed = False, True
    price(research_session, at=T0 + timedelta(seconds=30), value=1, tx="replacement")
    price(research_session, at=T0 + timedelta(hours=1), value=1000, tx="future")
    invalidate_derived(research_session, reason="TEST_REORG", cause={})
    research_session.commit()
    recover(research_session)
    current = research_session.scalar(select(LiveSignalOutcome).where(LiveSignalOutcome.horizon == "30s"))
    assert current is not None and current.id != old.id
    assert current.return_pct == Decimal("-0.5") and old.return_pct == 1
    assert current.previous_snapshot_id == old.id
    assert utc(current.evaluated_at) == EVALUATED
    assert current.observation_id != original.id and current.observation_root_id == original.id
    assert "future" not in str(current.input_manifest)


def test_missing_entry_remains_unknown_after_reorg_instead_of_reusing_old_price(research_session):
    _, _, _, old = complete(research_session)
    from smms.db.models import TradeEvent

    entry = research_session.scalar(select(TradeEvent).where(TradeEvent.tx_hash == "entry"))
    entry.canonical, entry.removed = False, True
    invalidate_derived(research_session, reason="TEST_REORG", cause={})
    research_session.commit()
    recover(research_session)
    current = research_session.scalar(select(LiveSignalObservation))
    assert current is not None and current.price is None
    assert research_session.scalar(select(LiveSignalOutcome)) is None
    archived = research_session.scalar(select(LiveSignalOutcome).where(LiveSignalOutcome.id == old.id)
                                      .execution_options(include_invalid_snapshots=True))
    assert archived.return_pct == 1


def test_repeated_transition_keeps_first_t0_when_rebuilding(research_session):
    candidate, original = observe(research_session)
    price(research_session, at=T0 + timedelta(seconds=20), value=99, tx="later")
    invalidate_derived(research_session, reason="TEST_REORG", cause={})
    research_session.commit()
    recover(research_session)
    current = record_observation(research_session, candidate, "WATCH", at=T0 + timedelta(seconds=20))
    assert current.id != original.id and current.price == 2
    assert utc(current.observed_at) == T0


def test_published_return_cannot_be_overwritten(research_session):
    _, _, _, outcome = complete(research_session)
    outcome.return_pct = 999
    with pytest.raises(ValueError, match="immutable"):
        research_session.flush()


def test_published_observation_cannot_be_overwritten(research_session):
    _, observation = observe(research_session)
    observation.price = 999
    with pytest.raises(ValueError, match="immutable"):
        research_session.flush()


def test_entry_and_outcome_record_exact_sources_and_dependencies(research_session):
    _, observation, _, outcome = complete(research_session)
    assert observation.input_manifest["digest"] and outcome.input_manifest["digest"]
    assert observation.input_manifest["parameters"]["cohort_policy"] == "recorded-transition-cohort-v1"
    assert any(d["kind"] == "live_signal_observations" and d["id"] == observation.id
               for d in outcome.input_manifest["dependencies"])
    assert "TradeEvent" in str(outcome.input_manifest)


def test_missing_entry_can_be_reconstructed_after_delayed_ingestion(research_session):
    seed(research_session)
    candidate = RadarEngine(research_session).ingest(research_session.scalar(select(Pair)), at=T0)[0]
    original = record_observation(research_session, candidate, "WATCH", at=T0)
    assert original.price is None
    evaluate_due(research_session, at=EVALUATED)
    price(research_session)
    price(research_session, at=T0 + timedelta(seconds=30), value=4, tx="late-endpoint")
    evaluate_due(research_session, at=T0 + timedelta(seconds=96))
    evaluate_due(research_session, at=T0 + timedelta(seconds=97))
    current = research_session.scalar(select(LiveSignalObservation))
    result = research_session.scalar(select(LiveSignalOutcome).where(LiveSignalOutcome.horizon == "30s"))
    assert current.id != original.id and current.price == 2 and original.price is None
    assert utc(current.observed_at) == T0
    assert result.return_pct == 1


def test_withdrawn_pair_does_not_resurrect_recorded_returns(research_session):
    _, _, _, old = complete(research_session)
    pair = research_session.scalar(select(Pair))
    pair.canonical, pair.removed = False, True
    invalidate_derived(research_session, reason="TEST_REORG", cause={})
    research_session.commit()
    recover(research_session)
    assert research_session.scalar(select(LiveSignalObservation)) is None
    assert research_session.scalar(select(LiveSignalOutcome)) is None
    summary = performance(research_session)
    assert summary["withdrawn_count"] == 1
    assert summary["horizons"]["30s"]["WATCH"]["sample_count"] == 0
    archived = research_session.scalar(select(LiveSignalOutcome).where(LiveSignalOutcome.id == old.id)
                                      .execution_options(include_invalid_snapshots=True))
    assert archived.return_pct == 1


def test_same_epoch_price_revision_invalidates_entry_and_return(research_session):
    from smms.db.models import TokenMarketSnapshot
    from smms.research.lineage import manifest
    from smms.research.publication import publish_snapshot

    seed(research_session)

    def snapshot(value):
        return publish_snapshot(research_session, TokenMarketSnapshot(
            chain="bsc", chain_id=56, token_address=next(p.token0_address for p in research_session.scalars(select(Pair))),
            pair_address=research_session.scalar(select(Pair.address)), source="fixture", as_of=T0, observed_at=T0,
            price_usd=value, status="KNOWN", source_epoch=0,
            input_manifest=manifest(kind="token_market_snapshots", parameters={}, facts={"price": value}),
        ))

    snapshot(2)
    candidate = RadarEngine(research_session).ingest(research_session.scalar(select(Pair)), at=T0)[0]
    old = record_observation(research_session, candidate, "WATCH", at=T0)
    price(research_session, at=T0 + timedelta(seconds=30), value=4, tx="endpoint")
    evaluate_due(research_session, at=EVALUATED)
    snapshot(4)
    research_session.commit()
    assert research_session.scalar(select(LiveSignalObservation)) is None
    assert research_session.scalar(select(LiveSignalOutcome)) is None
    recover(research_session)
    current = research_session.scalar(select(LiveSignalObservation))
    result = research_session.scalar(select(LiveSignalOutcome).where(LiveSignalOutcome.horizon == "30s"))
    assert current.id != old.id and current.price == 4 and old.price == 2
    assert result.return_pct == 0


def test_pending_unknown_audit_and_successful_retry_are_distinct_revisions(research_session):
    from smms.db.models import ForwardEvaluationJob

    _, observation = observe(research_session)
    evaluate_due(research_session, at=EVALUATED)
    pending = research_session.scalar(select(ForwardEvaluationJob).where(
        ForwardEvaluationJob.observation_id == observation.id, ForwardEvaluationJob.horizon == "30s"))
    unknown = research_session.scalar(select(LiveSignalOutcome).where(LiveSignalOutcome.horizon == "30s")
                                     .execution_options(include_invalid_snapshots=True))
    assert pending.status == "PENDING" and unknown.snapshot_status == "UNAVAILABLE"
    price(research_session, at=T0 + timedelta(seconds=30), value=4, tx="late")
    evaluate_due(research_session, at=T0 + timedelta(seconds=96))
    result = research_session.scalar(select(LiveSignalOutcome).where(LiveSignalOutcome.horizon == "30s"))
    assert result.previous_snapshot_id == unknown.id and unknown.return_pct is None
    assert pending.status == "COMPLETE" and pending.result_id == result.id


def test_postgres_concurrent_observation_writers_converge(research_session):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from sqlalchemy.orm import Session

    from smms.db.models import ForwardEvaluationJob, RadarCandidate

    if research_session.bind.dialect.name != "postgresql":
        pytest.skip("concurrent publication requires PostgreSQL")
    seed(research_session)
    price(research_session)
    candidate = RadarEngine(research_session).ingest(research_session.scalar(select(Pair)), at=T0)[0]
    candidate_id = candidate.id
    research_session.commit()
    ready = Barrier(2)

    def writer():
        with Session(research_session.bind) as session:
            value = session.get(RadarCandidate, candidate_id)
            ready.wait(timeout=5)
            row = record_observation(session, value, "WATCH", at=T0)
            session.commit()
            return row.id

    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(writer) for _ in range(2)]
        results = [job.result(timeout=20) for job in jobs]
    assert results[0] == results[1]
    assert len(research_session.scalars(select(ForwardEvaluationJob)).all()) == 8


def test_postgres_reorg_progresses_during_observation_read(research_session, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from sqlalchemy import text
    from sqlalchemy.orm import Session

    from smms.db.models import RadarCandidate
    from smms.radar import forward_research

    if research_session.bind.dialect.name != "postgresql":
        pytest.skip("independent reorg writer requires PostgreSQL")
    seed(research_session)
    price(research_session)
    candidate = RadarEngine(research_session).ingest(research_session.scalar(select(Pair)), at=T0)[0]
    candidate_id = candidate.id
    research_session.commit()
    ready, release = Event(), Event()
    original = forward_research.load_usd_prices

    def pause(*args, **kwargs):
        result = original(*args, **kwargs)
        ready.set()
        assert release.wait(timeout=8)
        return result

    monkeypatch.setattr(forward_research, "load_usd_prices", pause)

    def writer():
        with Session(research_session.bind) as session:
            row = session.get(RadarCandidate, candidate_id)
            with pytest.raises(ValueError, match="epoch"):
                record_observation(session, row, "WATCH", at=T0)
            session.rollback()

    with ThreadPoolExecutor(max_workers=1) as pool:
        job = pool.submit(writer)
        try:
            assert ready.wait(timeout=5)
            research_session.execute(text("SET LOCAL lock_timeout='3s'"))
            invalidate_derived(research_session, reason="TEST_REORG", cause={})
            research_session.commit()
        finally:
            release.set()
        job.result(timeout=15)
    assert research_session.scalar(select(LiveSignalObservation)) is None


def test_rollback_keeps_both_outcome_and_job_pending(research_session):
    from smms.db.models import ForwardEvaluationJob

    _, observation = observe(research_session)
    price(research_session, at=T0 + timedelta(seconds=30), value=4, tx="endpoint")
    research_session.commit()
    evaluate_due(research_session, at=EVALUATED)
    research_session.rollback()
    assert research_session.scalar(select(LiveSignalOutcome)) is None
    job = research_session.scalar(select(ForwardEvaluationJob).where(
        ForwardEvaluationJob.observation_id == observation.id, ForwardEvaluationJob.horizon == "30s"))
    assert job.status == "PENDING" and job.attempts == 0


def test_one_failed_result_does_not_abort_neighbor_job(research_session, monkeypatch):
    from smms.radar import live_forward

    _, observation = observe(research_session)
    price(research_session, at=T0 + timedelta(seconds=30), value=4, tx="endpoint")
    price(research_session, at=T0 + timedelta(seconds=60), value=4, tx="minute")
    original = live_forward.evaluate_observation

    def fail(session, row, **kwargs):
        if kwargs["horizon"] == "30s":
            raise ValueError("synthetic rejected result")
        return original(session, row, **kwargs)

    monkeypatch.setattr(live_forward, "evaluate_observation", fail)
    assert evaluate_due(research_session, at=T0 + timedelta(seconds=65)) == 1
    assert research_session.scalar(select(LiveSignalOutcome).where(LiveSignalOutcome.horizon == "1m")).observation_id == observation.id


def test_postgres_stale_first_lookup_cannot_move_first_t0(research_session, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from sqlalchemy.orm import Session

    from smms.db.models import RadarCandidate
    from smms.radar import forward_research

    if research_session.bind.dialect.name != "postgresql":
        pytest.skip("concurrent publication requires PostgreSQL")
    seed(research_session)
    price(research_session)
    price(research_session, at=T0 + timedelta(seconds=20), value=99, tx="later")
    candidate = RadarEngine(research_session).ingest(research_session.scalar(select(Pair)), at=T0)[0]
    candidate_id = candidate.id
    research_session.commit()
    ready = Barrier(2)
    original = forward_research.publish_observation

    def synchronize(*args, **kwargs):
        ready.wait(timeout=5)
        return original(*args, **kwargs)

    monkeypatch.setattr(forward_research, "publish_observation", synchronize)

    def writer(seconds):
        with Session(research_session.bind) as session:
            row = record_observation(session, session.get(RadarCandidate, candidate_id), "WATCH", at=T0 + timedelta(seconds=seconds))
            session.commit()
            return row.id, row.observed_at

    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(writer, seconds) for seconds in (0, 20)]
        results = [job.result(timeout=20) for job in jobs]
    assert results[0] == results[1]
    assert len(research_session.scalars(select(LiveSignalObservation).execution_options(include_invalid_snapshots=True)).all()) == 1


def test_entry_liquidity_comes_from_the_frozen_entry_pair(research_session):
    from smms.db.models import TokenMarketSnapshot

    _, candidate_obs = observe(research_session)
    # Use another recorded class to get a fresh T0 observation with both pools.
    from smms.db.models import RadarCandidate

    candidate = research_session.get(RadarCandidate, candidate_obs.candidate_id)
    token = candidate_obs.token_address
    entry_pair = candidate_obs.t0_snapshot_json["pair_address"]
    research_session.add(Pair(chain_id=56, address="other", token0_address=token, token1_address="quote", creation_timestamp=T0))
    for pair, liquidity in ((entry_pair, 100), ("other", 100000)):
        research_session.add(TokenMarketSnapshot(chain="bsc", chain_id=56, token_address=token,
            pair_address=pair, source="fixture", as_of=T0, observed_at=T0, status="KNOWN", price_usd=2,
            liquidity_usd=liquidity, market_cap_usd=liquidity * 10))
    RadarEngine(research_session).refresh(candidate, at=T0)
    current = record_observation(research_session, candidate, "INTERESTING", at=T0)
    assert current.t0_snapshot_json["pair_address"] == entry_pair
    assert current.liquidity == 100 and current.market_cap == 1000


def test_postgres_second_evaluator_does_not_wait_for_repair_cursor(research_session, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor, TimeoutError
    from threading import Event

    from sqlalchemy.orm import Session
    from test_forward_evaluation_integrity import market, observation

    from smms.radar import live_forward

    if research_session.bind.dialect.name != "postgresql":
        pytest.skip("concurrent worker claims require PostgreSQL")
    first, second = observation(research_session, suffix="first"), observation(research_session, suffix="second")
    for row in (first, second):
        market(research_session, row, 30, "2")
    live_forward._initialize_outcomes(research_session, batch_size=256)
    first_id = first.id
    research_session.commit()
    ready, release = Event(), Event()
    original = live_forward.evaluate_observation

    def pause(session, row, **kwargs):
        if row.id == first_id:
            ready.set()
            assert release.wait(timeout=10)
        return original(session, row, **kwargs)

    monkeypatch.setattr(live_forward, "evaluate_observation", pause)

    def worker():
        with Session(research_session.bind) as session:
            count = evaluate_due(session, at=EVALUATED, batch_size=1)
            session.commit()
            return count

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_job = pool.submit(worker)
        assert ready.wait(timeout=5)
        second_job = pool.submit(worker)
        try:
            timely = second_job.result(timeout=3) == 1
        except TimeoutError:
            timely = False
        finally:
            release.set()
        first_job.result(timeout=15)
        second_job.result(timeout=15)
    assert timely, "the repair cursor serialized otherwise independent due jobs"


def test_same_epoch_endpoint_withdrawal_reopens_completed_retry_job(research_session):
    from smms.db.models import ForwardEvaluationJob, TokenMarketSnapshot
    from smms.research.lineage import manifest
    from smms.research.publication import publish_snapshot

    _, observation = observe(research_session)

    def endpoint(value):
        return publish_snapshot(research_session, TokenMarketSnapshot(
            chain="bsc", chain_id=56, token_address=observation.token_address,
            pair_address=observation.t0_snapshot_json["pair_address"], source="endpoint",
            as_of=T0 + timedelta(seconds=30), observed_at=T0 + timedelta(seconds=30),
            status="KNOWN" if value else "UNAVAILABLE", price_usd=value, source_epoch=0,
            snapshot_status="VALID" if value else "UNAVAILABLE",
            input_manifest=manifest(kind="token_market_snapshots", parameters={}, facts={"value": value}),
        ))

    endpoint(4)
    evaluate_due(research_session, at=EVALUATED)
    job = research_session.scalar(select(ForwardEvaluationJob).where(
        ForwardEvaluationJob.observation_id == observation.id, ForwardEvaluationJob.horizon == "30s"))
    assert job.status == "COMPLETE"
    old_result = job.result_id
    endpoint(None)
    research_session.commit()
    recover(research_session)
    evaluate_due(research_session, at=T0 + timedelta(days=3))
    assert job.status == "PENDING" and job.result_id != old_result
    endpoint(6)
    assert evaluate_due(research_session, at=T0 + timedelta(days=3, seconds=61)) == 1
    result = research_session.scalar(select(LiveSignalOutcome).where(LiveSignalOutcome.horizon == "30s"))
    assert result.return_pct == 2 and job.result_id == result.id and job.status == "COMPLETE"


@pytest.mark.asyncio
async def test_forward_api_withholds_stale_results_and_retains_audit(coverage_session, monkeypatch):
    from contextlib import asynccontextmanager

    from httpx import ASGITransport, AsyncClient

    from smms.api import app as api
    from smms.api import radar, research

    session = coverage_session
    _, observation, _, outcome = await session.run_sync(complete)

    @asynccontextmanager
    async def scope(_settings):
        yield session

    for module in (api, radar, research):
        monkeypatch.setattr(module, "session_scope", scope)
    async with AsyncClient(transport=ASGITransport(app=api.app), base_url="http://test") as client:
        initial = (await client.get("/api/radar/live-performance")).json()
        assert initial["horizons"]["30s"]["WATCH"]["sample_count"] == 1
        await session.run_sync(lambda sync: invalidate_derived(sync, reason="TEST_REORG", cause={}))
        await session.commit()
        current = (await client.get("/api/radar/live-performance")).json()
        assert current["data_status"] == "REBUILDING" and current["withheld_count"] == 1
        assert current["horizons"]["30s"]["WATCH"]["sample_count"] == 0
        audit = (await client.get(f"/api/research/snapshots/live_signal_outcomes/{outcome.id}")).json()
        assert audit["audit_only"] and not audit["usable_current"] and audit["snapshot"]["return_pct"] == "1"
        history = (await client.get("/api/research/forward-jobs?limit=2")).json()
        assert history["audit_only"] and len(history["jobs"]) == 2 and history["next_cursor"]
        assert all(row["observation_id"] == observation.id for row in history["jobs"])


def test_completed_repair_repoints_job_to_current_result(research_session):
    from smms.db.models import ForwardEvaluationJob, TokenMarketSnapshot
    from smms.research.lineage import manifest
    from smms.research.publication import publish_snapshot

    _, observation = observe(research_session)

    def endpoint(value):
        return publish_snapshot(research_session, TokenMarketSnapshot(
            chain="bsc", chain_id=56, token_address=observation.token_address,
            pair_address=observation.t0_snapshot_json["pair_address"], source="endpoint",
            as_of=T0 + timedelta(seconds=30), observed_at=T0 + timedelta(seconds=30),
            status="KNOWN", price_usd=value, source_epoch=0,
            input_manifest=manifest(kind="token_market_snapshots", parameters={}, facts={"value": value}),
        ))

    endpoint(4)
    evaluate_due(research_session, at=EVALUATED)
    job = research_session.scalar(select(ForwardEvaluationJob).where(
        ForwardEvaluationJob.observation_id == observation.id, ForwardEvaluationJob.horizon == "30s"))
    original_result = job.result_id
    endpoint(6)
    research_session.commit()
    recover(research_session)
    result = research_session.scalar(select(LiveSignalOutcome).where(LiveSignalOutcome.horizon == "30s"))
    assert result.id != original_result and result.return_pct == 2
    evaluate_due(research_session, at=T0 + timedelta(seconds=61))
    research_session.commit()
    assert job.status == "COMPLETE" and job.result_id == result.id

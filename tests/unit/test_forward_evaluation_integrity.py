"""Public-path regressions for trustworthy live and historical evaluation."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_research_integrity import research_session as _research_session

from smms.backtest.signal_engine import evaluate_signal, generate_signal, run_backtest
from smms.db.models import (
    LiveSignalObservation,
    LiveSignalOutcome,
    MarketEvent,
    Pair,
    RadarCandidate,
    Token,
    TokenMarketSnapshot,
    TradeEvent,
)
from smms.radar.live_forward import evaluate_due, performance, record_observation

T0 = datetime(2026, 1, 1, tzinfo=UTC)
research_session = _research_session


def observation(session, *, suffix="a", price="1", basis="USD", observation_id=None):
    token = Token(chain="bsc", address=f"forward-{suffix}")
    session.add(token)
    session.flush()
    pair = Pair(chain_id=56, address=f"pair-{suffix}", token0_address=token.address,
                token1_address="quote", creation_timestamp=T0)
    candidate = RadarCandidate(token_id=token.id, discovered_at=T0, latest_seen_at=T0,
                               discovery_source="DEX_PAIR", discovery_reason="fixture")
    session.add_all([pair, candidate])
    session.flush()
    row = LiveSignalObservation(
        id=observation_id,
        candidate_id=candidate.id, token_id=token.id, chain="bsc", token_address=token.address,
        observed_at=T0, signal_class="HIGH_SIGNAL", price=Decimal(price),
        feature_version="f1", config_version="c1", model_version="m1",
        sample_group="HIGH_SIGNAL", t0_snapshot_json={
            "price": price, "price_basis": basis, "pair_address": pair.address,
        },
    )
    session.add(row)
    session.flush()
    return row


def market(session, row, seconds, price, *, metadata=None):
    event = MarketEvent(
        chain_id=56, event_type="SWAP", token_address=row.token_address,
        pair_address=row.t0_snapshot_json["pair_address"],
        tx_hash=f"{row.token_address}-{seconds}-{price}",
        timestamp=T0 + timedelta(seconds=seconds), price=Decimal(price), source="fixture",
        metadata_json={"price_basis": "USD", **(metadata or {})},
    )
    session.add(event)
    session.flush()
    return event


def outcome(session, row, horizon="30s"):
    return session.scalar(select(LiveSignalOutcome).where(
        LiveSignalOutcome.observation_id == row.id, LiveSignalOutcome.horizon == horizon,
    ).order_by(LiveSignalOutcome.id.desc()))


def test_thirty_second_horizon_cannot_accept_price_two_hours_later(research_session):
    row = observation(research_session)
    market(research_session, row, 7200, "3")

    evaluate_due(research_session, at=T0 + timedelta(hours=2))

    result = outcome(research_session, row)
    assert result.status == "PENDING"
    assert result.return_pct is None
    assert result.error_code == "MISSING_PRICE"


@pytest.mark.parametrize("prices,expected", [
    ([(10, "2"), (20, "1"), (30, "1")], Decimal("-0.5")),
    ([(30, "0.8")], Decimal("-0.2")),
])
def test_forward_drawdown_is_relative_to_running_price_peak(research_session, prices, expected):
    row = observation(research_session)
    for seconds, price in prices:
        market(research_session, row, seconds, price)

    evaluate_due(research_session, at=T0 + timedelta(seconds=30))

    result = outcome(research_session, row)
    assert result.status == "COMPLETE"
    assert result.max_drawdown_pct == expected


@pytest.mark.parametrize("marker", [
    {"removed": True}, {"orphaned": True}, {"confirmation_status": "orphaned"},
])
def test_orphan_price_cannot_be_endpoint_or_extremum(research_session, marker):
    row = observation(research_session)
    market(research_session, row, 10, "100", metadata=marker)
    market(research_session, row, 30, "100", metadata=marker)
    market(research_session, row, 31, "1.1")

    evaluate_due(research_session, at=T0 + timedelta(seconds=31))

    result = outcome(research_session, row)
    assert result.status == "COMPLETE"
    assert result.return_pct == Decimal("0.1")
    assert result.max_return_pct == Decimal("0.1")


@pytest.mark.parametrize("endpoint_seconds", [29, 30, 31])
def test_live_and_backtest_share_endpoint_and_drawdown_policy(research_session, endpoint_seconds):
    row = observation(research_session)
    points = [(10, "2"), (20, "1"), (endpoint_seconds, "1.2")]
    for seconds, price in points:
        market(research_session, row, seconds, price)
    signal = generate_signal("forward-a", T0, market={"price": "1"})
    historical = evaluate_signal(signal, [
        {"timestamp": T0 + timedelta(seconds=s), "price": p} for s, p in points
    ])[0]

    evaluate_due(research_session, at=T0 + timedelta(seconds=35))

    live = outcome(research_session, row)
    assert live.status == "COMPLETE"
    assert live.return_pct == historical.return_pct
    assert live.max_return_pct == historical.high_return
    assert live.max_drawdown_pct == historical.max_drawdown


def test_legacy_complete_result_is_preserved_and_excluded_from_current_stats(research_session):
    row = observation(research_session)
    legacy = LiveSignalOutcome(observation_id=row.id, horizon="30s",
                               target_at=T0 + timedelta(seconds=30), status="COMPLETE",
                               return_pct=Decimal(999))
    legacy.evaluation_version = "legacy-v1"
    research_session.add(legacy)
    market(research_session, row, 30, "1.2")

    evaluate_due(research_session, at=T0 + timedelta(seconds=30))

    results = research_session.scalars(select(LiveSignalOutcome).where(
        LiveSignalOutcome.observation_id == row.id, LiveSignalOutcome.horizon == "30s",
    )).all()
    assert len(results) == 2
    assert legacy.return_pct == 999
    summary = performance(research_session)
    assert summary["horizons"]["30s"]["HIGH_SIGNAL"]["sample_count"] == 1
    assert summary["horizons"]["30s"]["HIGH_SIGNAL"]["median_return"] == pytest.approx(0.2)


def test_processing_batch_is_bounded_and_later_tasks_make_progress(research_session):
    rows = [observation(research_session, suffix=str(i)) for i in range(5)]
    for row in rows:
        market(research_session, row, 30, "1.2")

    assert evaluate_due(research_session, at=T0 + timedelta(seconds=30), batch_size=2) == 2
    complete = research_session.scalars(select(LiveSignalOutcome).where(
        LiveSignalOutcome.status == "COMPLETE",
    )).all()
    assert len(complete) == 2
    assert evaluate_due(research_session, at=T0 + timedelta(seconds=30), batch_size=2) == 2
    assert evaluate_due(research_session, at=T0 + timedelta(seconds=30), batch_size=2) == 1


def test_backtest_accepts_bounded_late_endpoint_as_its_observed_path():
    signal = generate_signal("t", T0, market={"price": "1"})

    result = evaluate_signal(signal, [{
        "timestamp": T0 + timedelta(seconds=31), "price": "2",
    }])[0]

    assert result.available is True
    assert result.return_pct == result.high_return == Decimal(1)
    assert result.low_return == Decimal(0)
    assert result.max_drawdown == Decimal(0)


@pytest.mark.parametrize("invalid", ["NaN", "Infinity", "-1", "0"])
def test_backtest_ignores_invalid_price_in_endpoint_and_path(invalid):
    signal = generate_signal("t", T0, market={"price": "1"})

    result = evaluate_signal(signal, [
        {"timestamp": T0 + timedelta(seconds=30), "price": invalid},
        {"timestamp": T0 + timedelta(seconds=31), "price": "1.2"},
    ])[0]

    assert result.available is True
    assert result.return_pct == result.high_return == Decimal("0.2")
    assert result.low_return == Decimal(0)
    assert result.max_drawdown == Decimal(0)


def usd_snapshot(session, row, seconds, price, *, as_of_seconds=None, metadata=None):
    snapshot = TokenMarketSnapshot(
        chain="bsc", chain_id=56, token_address=row.token_address,
        pair_address=row.t0_snapshot_json["pair_address"], source="DEXSCREENER",
        observed_at=T0 + timedelta(seconds=seconds),
        as_of=T0 + timedelta(seconds=seconds if as_of_seconds is None else as_of_seconds),
        price_usd=Decimal(price), metadata_json=metadata or {},
    )
    session.add(snapshot)
    session.flush()
    return snapshot


def test_usd_snapshot_path_is_evaluated_with_endpoint_provenance(research_session):
    row = observation(research_session)
    snapshot = usd_snapshot(research_session, row, 30, "1.2")

    evaluate_due(research_session, at=T0 + timedelta(seconds=30))

    result = outcome(research_session, row)
    assert result.status == "COMPLETE"
    assert result.return_pct == Decimal("0.2")
    assert result.endpoint_at.replace(tzinfo=UTC) == T0 + timedelta(seconds=30)
    assert result.provenance_json["endpoint"]["model"] == "TokenMarketSnapshot"
    assert result.provenance_json["endpoint"]["id"] == snapshot.id
    assert result.provenance_json["price_basis"] == "USD"


def test_quote_price_is_not_compared_with_usd_entry(research_session):
    row = observation(research_session)
    market(research_session, row, 30, "1000", metadata={"price_basis": "HUMAN_QUOTE_PRICE"})
    usd_snapshot(research_session, row, 31, "1.2")

    evaluate_due(research_session, at=T0 + timedelta(seconds=31))

    assert outcome(research_session, row).return_pct == Decimal("0.2")


def test_unknown_entry_basis_stays_unavailable(research_session):
    row = observation(research_session, basis="UNKNOWN")
    market(research_session, row, 30, "2")

    evaluate_due(research_session, at=T0 + timedelta(seconds=30))

    result = outcome(research_session, row)
    assert result.status == "PENDING"
    assert result.error_code == "PRICE_BASIS_UNKNOWN"
    assert result.return_pct is None


def test_canonical_usd_trade_is_usable_without_generic_market_price(research_session):
    row = observation(research_session)
    research_session.add(TradeEvent(
        chain="bsc", token_address=row.token_address,
        pair_address=row.t0_snapshot_json["pair_address"], trader_address="buyer",
        tx_hash="usd-trade", timestamp=T0 + timedelta(seconds=30), side="BUY",
        price_usd=Decimal("1.2"), source="fixture",
    ))
    research_session.flush()

    evaluate_due(research_session, at=T0 + timedelta(seconds=30))

    assert outcome(research_session, row).return_pct == Decimal("0.2")


@pytest.mark.parametrize("seconds,as_of_seconds,usable", [
    (0, 0, True), (-31, -31, False), (1, 1, False), (-1, 1, False),
])
def test_recorded_entry_uses_fresh_asof_usd_evidence(
    research_session, seconds, as_of_seconds, usable,
):
    template = observation(research_session)
    candidate = research_session.get(RadarCandidate, template.candidate_id)
    candidate.features_json = {"price": "999", "market_cap": "UNKNOWN", "liquidity": "UNKNOWN"}
    snapshot = usd_snapshot(research_session, template, seconds, "1.5", as_of_seconds=as_of_seconds)

    row = record_observation(research_session, candidate, "WATCH", at=T0)

    assert row.price == (Decimal("1.5") if usable else None)
    assert row.t0_snapshot_json["price_basis"] == ("USD" if usable else "UNKNOWN")
    if usable:
        assert row.t0_snapshot_json["entry_source"]["id"] == snapshot.id


def test_orphan_pair_cannot_supply_a_usd_endpoint(research_session):
    row = observation(research_session)
    usd_snapshot(research_session, row, 30, "2")
    pair = research_session.scalar(select(Pair))
    pair.canonical = False
    pair.removed = True
    research_session.flush()

    evaluate_due(research_session, at=T0 + timedelta(seconds=30))

    assert outcome(research_session, row).status == "PENDING"


def test_pending_retry_and_cursor_survive_a_new_session(research_session):
    row = observation(research_session)
    evaluate_due(research_session, at=T0 + timedelta(seconds=30))
    pending = outcome(research_session, row)
    assert pending.error_code == "MISSING_PRICE"
    assert pending.next_evaluation_at.replace(tzinfo=UTC) == T0 + timedelta(seconds=90)
    market(research_session, row, 30, "1.2")  # delayed ingestion, original event time
    row_id = row.id
    research_session.commit()

    with Session(research_session.get_bind()) as restarted:
        assert evaluate_due(restarted, at=T0 + timedelta(seconds=60)) == 0
        assert evaluate_due(restarted, at=T0 + timedelta(seconds=91)) == 1
        results = restarted.scalars(select(LiveSignalOutcome).where(
            LiveSignalOutcome.observation_id == row_id, LiveSignalOutcome.horizon == "30s",
        )).all()
        assert len(results) == 1
        assert results[0].return_pct == Decimal("0.2")


def test_missing_price_does_not_starve_later_due_observation(research_session):
    observation(research_session, suffix="missing")
    priced = observation(research_session, suffix="priced")
    market(research_session, priced, 30, "1.2")

    assert evaluate_due(research_session, at=T0 + timedelta(seconds=30), batch_size=1) == 0
    assert evaluate_due(research_session, at=T0 + timedelta(seconds=30), batch_size=1) == 1
    assert outcome(research_session, priced).status == "COMPLETE"


def test_endpoint_has_signed_offset_and_tolerance(research_session):
    row = observation(research_session)
    market(research_session, row, 29, "1.2")

    evaluate_due(research_session, at=T0 + timedelta(seconds=30))

    result = outcome(research_session, row)
    assert result.endpoint_offset_seconds == Decimal(-1)
    assert result.endpoint_tolerance_seconds == 5
    assert result.provenance_json["path_count"] == 2
    assert len(result.provenance_json["path_digest"]) == 64


def test_even_sample_median_is_mean_of_two_middle_returns(research_session):
    for suffix, price in [("low", "1.1"), ("high", "1.9")]:
        row = observation(research_session, suffix=suffix)
        market(research_session, row, 30, price)
    evaluate_due(research_session, at=T0 + timedelta(seconds=30))

    summary = performance(research_session)

    assert summary["horizons"]["30s"]["HIGH_SIGNAL"]["median_return"] == pytest.approx(0.5)


def test_new_observation_enqueues_horizons_in_its_own_transaction(research_session):
    template = observation(research_session)
    candidate = research_session.get(RadarCandidate, template.candidate_id)
    usd_snapshot(research_session, template, 0, "1")

    row = record_observation(research_session, candidate, "WATCH", at=T0)

    tasks = research_session.scalars(select(LiveSignalOutcome).where(
        LiveSignalOutcome.observation_id == row.id,
    )).all()
    assert {item.horizon for item in tasks} == {"30s", "1m", "5m", "15m", "30m", "1h", "4h", "24h"}


def test_repair_sweep_cannot_permanently_skip_later_visible_lower_id(research_session):
    high = observation(research_session, suffix="high-id", observation_id=10)
    market(research_session, high, 30, "1.2")
    assert evaluate_due(research_session, at=T0 + timedelta(seconds=30)) == 1
    late = observation(research_session, suffix="late-id", observation_id=9)
    market(research_session, late, 30, "1.2")

    evaluate_due(research_session, at=T0 + timedelta(seconds=30))
    evaluate_due(research_session, at=T0 + timedelta(seconds=30))

    assert outcome(research_session, late) is not None
    assert outcome(research_session, late).return_pct == Decimal("0.2")


def test_large_finite_return_does_not_poison_neighboring_due_tasks(research_session):
    huge = observation(research_session, suffix="huge", price="0.000000000001")
    normal = observation(research_session, suffix="normal")
    market(research_session, huge, 30, "1")
    market(research_session, normal, 30, "1.2")

    assert evaluate_due(research_session, at=T0 + timedelta(seconds=30)) == 2
    huge_id, normal_id = huge.id, normal.id
    research_session.commit()
    with Session(research_session.get_bind()) as restarted:
        results = {r.observation_id: r for r in restarted.scalars(select(LiveSignalOutcome).where(
            LiveSignalOutcome.horizon == "30s",
        ))}
        assert results[huge_id].return_pct == Decimal(999999999999)
        assert results[normal_id].return_pct == Decimal("0.2")


def test_postgresql_out_of_order_commits_are_eventually_initialized(research_session):
    if research_session.get_bind().dialect.name != "postgresql":
        pytest.skip("requires PostgreSQL commit visibility and independent connections")
    with Session(research_session.get_bind()) as delayed:
        late = observation(delayed, suffix="uncommitted")
        market(delayed, late, 30, "1.2")
        early = observation(research_session, suffix="committed")
        market(research_session, early, 30, "1.2")
        assert late.id < early.id
        late_id = late.id
        research_session.commit()
        assert evaluate_due(research_session, at=T0 + timedelta(seconds=30)) == 1
        research_session.commit()
        delayed.commit()

    evaluate_due(research_session, at=T0 + timedelta(seconds=30))
    evaluate_due(research_session, at=T0 + timedelta(seconds=30))

    result = outcome(research_session, research_session.get(LiveSignalObservation, late_id))
    assert result is not None
    assert result.status == "COMPLETE"


def test_performance_asof_does_not_read_later_completed_results(research_session):
    row = observation(research_session)
    market(research_session, row, 30, "1.2")
    evaluate_due(research_session, at=T0 + timedelta(seconds=30))

    past = performance(research_session, at=T0 + timedelta(seconds=10))
    current = performance(research_session, at=T0 + timedelta(seconds=30))

    assert past["horizons"]["30s"]["HIGH_SIGNAL"]["sample_count"] == 0
    assert current["horizons"]["30s"]["HIGH_SIGNAL"]["sample_count"] == 1


def test_backtest_summary_identifies_evaluation_recipe():
    result = run_backtest([{
        "token": "t", "timestamp": T0, "market": {"price": 1, "market_score": 100},
        "prices": [{"timestamp": T0 + timedelta(seconds=30), "price": 2}],
    }])

    assert result.summary["evaluation_version"] == "price-path-v2"


def test_equal_distance_endpoint_prefers_earlier_sample(research_session):
    row = observation(research_session)
    market(research_session, row, 31, "9")
    market(research_session, row, 29, "1.2")

    evaluate_due(research_session, at=T0 + timedelta(seconds=35))

    result = outcome(research_session, row)
    assert result.endpoint_offset_seconds == Decimal(-1)
    assert result.return_pct == result.max_return_pct == Decimal("0.2")

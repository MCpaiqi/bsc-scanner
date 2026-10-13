from datetime import UTC, datetime, timedelta
from decimal import Decimal

from smms.backtest.signal_engine import (
    HORIZONS,
    SignalWeights,
    as_of,
    baseline_comparison,
    evaluate_signal,
    generate_signal,
    run_backtest,
    walk_forward,
)


def test_point_in_time_excludes_future_features_and_entry_price() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    prices = [{"timestamp": t0 - timedelta(seconds=1), "price": "1"}, {"timestamp": t0 + timedelta(seconds=30), "price": "2"}]
    signal = generate_signal("T", t0, market={"market_score": 80, "liquidity": 1000}, smart_money={"smart_money_score": 90}, risk={"overall_risk": "LOW"}, early_buyers=[{"timestamp": t0 + timedelta(seconds=1)}], prices=prices)
    assert signal.entry_price == Decimal(1)
    assert signal.data_status == "OK"
    assert as_of(prices, t0) == [prices[0]]


def test_future_trade_exclusion_and_missing_price() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    signal = generate_signal("T", t0, market={"market_score": 90}, smart_money={"smart_money_score": 90}, risk={"overall_risk": "LOW"}, prices=[{"timestamp": t0 + timedelta(seconds=1), "price": "2"}])
    assert signal.data_status == "INSUFFICIENT_DATA"
    assert all(not item.available for item in evaluate_signal(signal, [{"timestamp": t0 + timedelta(seconds=30), "price": "3"}]))


def test_evaluation_windows_hits_and_rug() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    signal = generate_signal("T", t0, market={"market_score": 80, "liquidity": 1000}, smart_money={"smart_money_score": 80}, risk={"overall_risk": "LOW"}, prices=[{"timestamp": t0, "price": "1"}])
    evaluations = evaluate_signal(signal, [{"timestamp": t0 + timedelta(seconds=30), "price": "1.25"}, {"timestamp": t0 + timedelta(minutes=1), "price": "0.1"}])
    assert len(evaluations) == len(HORIZONS)
    assert "25%" in evaluations[0].hit_targets
    assert evaluations[1].rug


def test_duplicate_baseline_and_walk_forward() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    row = {"token": "T", "timestamp": t0, "market": {"market_score": 90, "liquidity": 1000, "price": 1}, "smart_money": {"smart_money_score": 90}, "risk": {"overall_risk": "LOW"}, "prices": [{"timestamp": t0, "price": 1}, {"timestamp": t0 + timedelta(minutes=1), "price": 2}]}
    result = run_backtest([row, row], threshold=Decimal(50))
    assert len(result.signals) == 1
    assert result.model_version == "signal-v1"
    comparison = baseline_comparison([row])
    assert "smart_money" in comparison and "market_only" in comparison
    wf = walk_forward([dict(row, timestamp=t0 + timedelta(days=i)) for i in range(5)])
    assert wf["model_version"] == "signal-v1"


def test_weights_are_configurable_and_zero_rejected() -> None:
    weights = SignalWeights.from_mapping({"market": 1, "smart_money": 0, "early_buyer": 0, "liquidity": 0, "risk": 0})
    assert weights.normalized()["market"] == Decimal(1)
    try:
        SignalWeights.from_mapping({"market": 0, "smart_money": 0, "early_buyer": 0, "liquidity": 0, "risk": 0})
    except ValueError:
        pass
    else:
        raise AssertionError("zero weights must fail")

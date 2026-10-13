from datetime import UTC, datetime, timedelta
from decimal import Decimal

from smms.backtest.signal_engine import evaluate_signal, generate_signal, run_backtest, walk_forward

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def signal(**kwargs):
    return generate_signal("T", T0, market={"market_score": 10, "liquidity": 1000}, smart_money={"smart_money_score": 100}, risk={"overall_risk": "LOW"}, prices=[{"timestamp": T0, "price": 100}], **kwargs)


def test_market_only_does_not_use_smart_money() -> None:
    assert signal(strategy="composite").score > signal(strategy="market_only").score
    assert signal(strategy="market_only").score < Decimal(20)
    assert signal(strategy="market_only").confidence_components["smart_money"] == 0
    assert signal(strategy="market_only").confidence_components["risk"] == 0


def test_horizon_metrics_are_independent_and_one_signal_is_not_eight_samples() -> None:
    prices = [{"timestamp": T0, "price": 100}] + [{"timestamp": T0 + delta, "price": 110} for _, delta in __import__("smms.backtest.signal_engine", fromlist=["HORIZONS"]).HORIZONS]
    row = {"token": "T", "timestamp": T0, "market": {"market_score": 100, "liquidity": 100000}, "smart_money": {"smart_money_score": 100}, "risk": {"overall_risk": "LOW"}, "prices": prices}
    summary = run_backtest([row]).summary
    assert summary["unique_signal_count"] == 1
    assert summary["sample_size"] == 1
    assert all(metric["sample_size"] == 1 for metric in summary["per_horizon_metrics"].values())


def test_one_minute_data_cannot_cover_24_hours() -> None:
    result = evaluate_signal(signal(), [{"timestamp": T0 + timedelta(minutes=1), "price": 110}])
    assert result[1].available
    assert not result[-1].available
    assert result[-1].reason == "INSUFFICIENT_HORIZON_COVERAGE"


def test_true_peak_to_trough_drawdown() -> None:
    result = evaluate_signal(signal(), [{"timestamp": T0 + timedelta(seconds=10), "price": 150}, {"timestamp": T0 + timedelta(seconds=20), "price": 90}, {"timestamp": T0 + timedelta(seconds=30), "price": 130}])[0]
    assert result.max_drawdown == Decimal("-0.4")
    assert result.max_adverse_excursion == Decimal("-0.1")
    assert result.max_favorable_excursion == Decimal("0.5")


def test_future_snapshots_fail_closed() -> None:
    future = T0 + timedelta(seconds=1)
    for name in ("market", "smart_money", "risk"):
        kwargs = {"market": {"market_score": 90, "liquidity": 1000}, "smart_money": {"smart_money_score": 90}, "risk": {"overall_risk": "LOW"}, "prices": [{"timestamp": T0, "price": 1}]}
        kwargs[name]["as_of"] = future
        result = generate_signal("T", T0, **kwargs)
        assert result.entry_price is None
        assert result.data_status == "INSUFFICIENT_DATA"


def test_unknown_risk_degrades_confidence() -> None:
    known = signal()
    unknown = generate_signal("T", T0, market={"market_score": 10, "liquidity": 1000}, smart_money={"smart_money_score": 100}, risk={"overall_risk": "UNKNOWN"}, prices=[{"timestamp": T0, "price": 100}])
    assert unknown.confidence < known.confidence
    assert unknown.data_status == "DEGRADED"
    assert unknown.confidence_components["risk"] == 0


def test_high_risk_is_not_entry_eligible() -> None:
    result = generate_signal("T", T0, market={"market_score": 100, "liquidity": 100000}, smart_money={"smart_money_score": 100}, risk={"overall_risk": "HIGH"}, prices=[{"timestamp": T0, "price": 1}])
    assert result.signal == "HIGH_RISK"
    assert not result.entry_eligible


def test_stale_entry_price_is_rejected() -> None:
    result = generate_signal("T", T0, market={"market_score": 100, "liquidity": 1000}, smart_money={"smart_money_score": 100}, risk={"overall_risk": "LOW"}, prices=[{"timestamp": T0 - timedelta(seconds=301), "price": 1}], max_entry_price_age_seconds=300)
    assert result.entry_price is None
    assert "STALE_ENTRY_PRICE" in result.risk_flags


def test_model_config_version_is_reproducible() -> None:
    a = signal(model_version="signal-v2", config_version="cfg-2")
    b = signal(model_version="signal-v2", config_version="cfg-2")
    changed = signal(model_version="signal-v2", config_version="cfg-3")
    assert a.config_hash == b.config_hash
    assert a.config_hash != changed.config_hash


def test_walk_forward_fold_isolation() -> None:
    rows = [{"token": str(i), "timestamp": T0 + timedelta(days=i)} for i in range(20)]
    folds = walk_forward(rows, n_folds=3)["folds"]
    assert folds
    assert all(fold["train"][1] < fold["validation"][0] <= fold["validation"][1] < fold["test"][0] for fold in folds)


def test_duplicate_evaluation_is_idempotent() -> None:
    row = {"token": "T", "timestamp": T0, "market": {"market_score": 100, "liquidity": 100000}, "smart_money": {"smart_money_score": 100}, "risk": {"overall_risk": "LOW"}, "prices": [{"timestamp": T0, "price": 1}, {"timestamp": T0 + timedelta(minutes=1), "price": 2}]}
    result = run_backtest([row, row])
    assert result.summary["unique_signal_count"] == 1
    assert len(result.evaluations) == 8

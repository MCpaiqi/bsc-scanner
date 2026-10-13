from decimal import Decimal
from types import SimpleNamespace

from smms.smart_money import (
    SmartMoneyMetrics,
    aggregate_token_smart_money,
    calculate_smart_money_score,
    exponential_time_decay,
)


def test_high_quality_wallet_has_explainable_high_score_and_confidence():
    result = calculate_smart_money_score(
        SmartMoneyMetrics(
            win_rate=Decimal("0.85"), avg_roi=Decimal("0.8"), median_roi=Decimal("0.55"),
            early_entry_rate=Decimal("0.8"), high_multiple_rate=Decimal("0.5"),
            consistency_rate=Decimal("0.8"), risk_adjusted_return=Decimal("0.7"),
            sample_size=20, data_completeness=Decimal(1), historical_coverage=Decimal(1),
        )
    )
    assert result.score > 60
    assert result.confidence == "HIGH"
    assert result.contributions["early_entry"] > 0
    assert result.contributions["rug_penalty"] <= 0


def test_low_quality_and_rug_heavy_wallet_is_penalized():
    low = calculate_smart_money_score(SmartMoneyMetrics(win_rate=Decimal("0.2"), rug_exposure=Decimal("0.8"), bot_probability=Decimal("0.7"), sample_size=12))
    assert low.score < 20
    assert low.contributions["rug_penalty"] < 0
    assert low.confidence == "MEDIUM"


def test_small_sample_and_single_outlier_do_not_look_reliable():
    small = calculate_smart_money_score(SmartMoneyMetrics(win_rate=Decimal(1), avg_roi=Decimal(10), sample_size=1))
    assert small.score > 0
    assert small.confidence == "LOW"
    assert calculate_smart_money_score(SmartMoneyMetrics(avg_roi=Decimal(10), median_roi=Decimal(0), sample_size=8)).score < 20


def test_bot_and_wash_penalties_are_independent_components():
    result = calculate_smart_money_score(SmartMoneyMetrics(win_rate=Decimal("0.8"), bot_probability=Decimal("0.9"), wash_trading_probability=Decimal("0.9"), sample_size=10))
    assert result.contributions["bot_penalty"] < 0
    assert result.contributions["wash_penalty"] < 0


def test_time_decay_is_configurable_and_monotonic():
    assert exponential_time_decay(0, 180) == Decimal(1)
    assert exponential_time_decay(180, 180) < Decimal("0.51")
    assert exponential_time_decay(365, 180) < exponential_time_decay(30, 180)


def test_token_aggregation_deduplicates_cluster_and_reports_flows():
    profile_a = SimpleNamespace(wallet="a", smart_money_score=Decimal(80), behavioral_indicators={"cluster_id": "c1"})
    profile_b = SimpleNamespace(wallet="b", smart_money_score=Decimal(75), behavioral_indicators={"cluster_id": "c1"})
    profile_c = SimpleNamespace(wallet="c", smart_money_score=Decimal(70), behavioral_indicators={})
    summary = aggregate_token_smart_money(
        "token",
        [profile_a, profile_b, profile_c],
        [
            {"token_address": "token", "wallet_address": "a", "side": "BUY", "usd_value": "100"},
            {"token_address": "token", "wallet_address": "c", "side": "SELL", "usd_value": "25"},
        ],
    )
    assert summary.smart_wallet_count == 2
    assert summary.smart_money_buy_usd == Decimal(100)
    assert summary.smart_money_sell_usd == Decimal(25)
    assert summary.smart_money_net_flow == Decimal(75)


def test_token_aggregation_without_profiles_is_unknown():
    summary = aggregate_token_smart_money("token", [], [])
    assert summary.data_status == "UNKNOWN"
    assert summary.smart_money_score is None


def test_token_aggregation_excludes_smart_wallets_that_did_not_trade_token():
    participant = SimpleNamespace(
        wallet="participant", smart_money_score=Decimal(70), behavioral_indicators={}
    )
    unrelated = SimpleNamespace(
        wallet="unrelated", smart_money_score=Decimal(100), behavioral_indicators={}
    )

    summary = aggregate_token_smart_money(
        "token",
        [participant, unrelated],
        [
            {
                "token_address": "token",
                "wallet_address": "participant",
                "side": "BUY",
                "usd_value": "10",
            }
        ],
    )

    assert summary.smart_wallet_count == 1
    assert summary.smart_money_score == Decimal(70)

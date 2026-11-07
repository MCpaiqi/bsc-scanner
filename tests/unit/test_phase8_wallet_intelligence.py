from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from smms.smart_money import (
    build_wallet_knowledge_graph,
    build_wallet_profile,
    calculate_smart_money_alpha,
    detect_wallet_clusters,
    replay_historical_smart_money,
    smart_money_flow,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def trade(wallet, token, side, minutes, value=10, **metadata):
    return {
        "wallet_address": wallet,
        "token_address": token,
        "side": side,
        "timestamp": T0 + timedelta(minutes=minutes),
        "usd_value": value,
        "metadata_json": metadata,
    }


def test_graph_detects_synchronized_co_buy_and_funding_cluster_at_cutoff():
    rows = [
        trade("a", "meme", "BUY", 1, funded_by="funder"),
        trade("b", "meme", "BUY", 3),
        trade("a", "meme", "SELL", 20),
        trade("future", "meme", "BUY", 90),
    ]
    graph = build_wallet_knowledge_graph(rows, as_of=T0 + timedelta(minutes=10))
    clusters = detect_wallet_clusters(graph)
    assert {r.kind for r in graph.relationships} >= {"bought_token", "traded_together", "funded_wallet"}
    assert any(set(cluster.wallets) >= {"a", "b"} for cluster in clusters)
    assert all(event.wallet != "future" for event in graph.trades)


def test_alpha_score_exposes_four_weighted_explainable_components():
    profile = build_wallet_profile(
        "a",
        performances=[
            {"wallet_address": "a", "token_address": "meme", "entry_time": T0, "exit_time": T0 + timedelta(hours=1), "total_buy": 10, "realized_pnl": 10, "roi": 1, "multiple": 2, "closed": True}
        ],
    )
    score = calculate_smart_money_alpha(profile, network_influence=Decimal("0.5"))
    assert 0 <= score.score <= 100
    assert set(score.contributions) == {"historical_skill", "current_behavior", "network_influence", "risk_adjustment"}
    assert set(score.explanation) == set(score.contributions)


def test_flow_respects_window_and_as_of_without_future_data():
    rows = [trade("a", "meme", "BUY", 0, 10), trade("a", "meme", "SELL", 2, 4), trade("a", "meme", "BUY", 20, 100)]
    result = smart_money_flow(rows, token="meme", smart_wallets=["a"], window="5m", as_of=T0 + timedelta(minutes=5))
    assert result["inflow"] == Decimal(10)
    assert result["outflow"] == Decimal(4)
    assert result["net_flow"] == Decimal(6)
    empty = smart_money_flow(rows, token="other", smart_wallets=["a"], window="5m", as_of=T0 + timedelta(minutes=5))
    assert empty["data_status"] == "UNKNOWN"


def test_historical_replay_returns_recurring_early_wallets_only_at_cutoff():
    winners = [
        {"address": "one", "first_seen_at": T0, "ath_multiple": 50},
        {"address": "two", "first_seen_at": T0 + timedelta(hours=1), "ath_multiple": 100},
        {"address": "future", "first_seen_at": T0 + timedelta(days=2), "ath_multiple": 100},
    ]
    rows = [trade("a", "one", "BUY", 5), trade("a", "two", "BUY", 65), trade("future-wallet", "future", "BUY", 3000)]
    result = replay_historical_smart_money(winners, rows, as_of=T0 + timedelta(days=1))
    assert [row["token"] for row in result["successful_tokens"]] == ["one", "two"]
    assert result["recurring_wallets"] == ["a"]


@pytest.mark.parametrize("missing", [None, "NaN", "Infinity", "-1"])
def test_flow_missing_usd_cannot_be_coerced_to_zero(missing):
    result = smart_money_flow([trade("a", "meme", "BUY", 1, 10),
                               trade("a", "meme", "SELL", 2, missing)],
                              token="meme", smart_wallets=["a"], window="5m",
                              as_of=T0 + timedelta(minutes=5))
    assert result["inflow"] == 10
    assert result["outflow"] is None and result["net_flow"] is None
    assert result["data_status"] == "DEGRADED"


def test_observed_zero_usd_remains_known():
    result = smart_money_flow([trade("a", "meme", "BUY", 1, 0)], token="meme",
                              smart_wallets=["a"], as_of=T0 + timedelta(minutes=5))
    assert result["net_flow"] == 0 and result["data_status"] == "OBSERVED"


def test_replay_discloses_graph_source_clipping():
    result = replay_historical_smart_money(
        [{"address": "meme", "first_seen_at": T0, "ath_multiple": 2}],
        [trade("a", "meme", "BUY", 1), trade("b", "meme", "BUY", 2)],
        as_of=T0 + timedelta(minutes=5), graph_parameters={"max_source_events": 1})
    assert result["truncated"] is True
    assert "source_event_limit" in result["truncation_reasons"]

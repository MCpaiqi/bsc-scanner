from datetime import UTC, datetime
from decimal import Decimal

from smms.risk import (
    analyze_behavior_risk,
    analyze_contract_risk,
    analyze_holder_risk,
    analyze_liquidity_risk,
    analyze_token_risk,
)


def test_unknown_is_preserved_when_contract_evidence_is_missing() -> None:
    result = analyze_contract_risk({})
    assert result.level == "UNKNOWN"
    assert "contract_evidence_unavailable" in result.evidence


def test_mintable_blacklist_and_high_tax_contract_is_not_safe() -> None:
    result = analyze_contract_risk({
        "code_exists": True, "owner": "0xowner", "mintable": True,
        "blacklist": True, "buy_tax": 15, "sell_tax": 25,
        "upgradeable": True, "ownership_renounced": False,
    })
    assert result.level in {"HIGH", "CRITICAL"}
    assert {"mintable", "blacklist", "high_buy_tax", "high_sell_tax"} <= set(result.evidence)


def test_low_liquidity_and_removal_are_high_risk() -> None:
    result = analyze_liquidity_risk({
        "current_liquidity": Decimal(5_000),
        "initial_liquidity": Decimal(100_000),
        "current_market_cap": Decimal(1_000_000),
    })
    assert result.level in {"HIGH", "CRITICAL"}
    assert "liquidity_removed" in result.evidence


def test_protocol_addresses_are_excluded_from_holder_concentration() -> None:
    result = analyze_holder_risk(
        {},
        [
            {"kind": "lp", "share": 90},
            {"kind": "router", "share": 5},
            {"kind": "wallet", "share": 5},
        ],
    )
    assert result.level == "LOW"
    assert result.details["top_holder"] == Decimal(5)


def test_bot_and_wash_probabilities_include_evidence() -> None:
    trades = [
        {"wallet_address": "a", "side": "BUY", "usd_value": 10},
        {"wallet_address": "a", "side": "SELL", "usd_value": 10},
    ] * 12
    result = analyze_behavior_risk(trades)
    assert result.bot_probability is not None and result.bot_probability > 0
    assert result.wash_probability is not None and result.wash_probability > 0
    assert "high_frequency" in result.evidence
    assert "repeated_size" in result.evidence


def test_overall_risk_keeps_unknown_and_is_independent_from_smart_money() -> None:
    snapshot = analyze_token_risk({"address": "Token", "metadata_json": {}}, as_of=datetime(2026, 1, 1, tzinfo=UTC))
    assert snapshot.overall_risk == "UNKNOWN"
    assert snapshot.contract_risk.level == "UNKNOWN"
    assert snapshot.bot_probability is None
    assert snapshot.risk_version == "risk-v1"

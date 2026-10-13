"""Deterministic, configurable attention priority, independent of trading signals."""

from decimal import Decimal

from .config import RadarConfig


def priority(features: dict, config: RadarConfig) -> dict:
    def known(key):
        value = features.get(key, "UNKNOWN")
        return None if value in (None, "UNKNOWN") else Decimal(str(value))

    age, buys, sells = (known(k) for k in ("age_seconds", "buy_count_5m", "sell_count_5m"))
    smart, liquidity = (known(k) for k in ("smart_money_score", "liquidity"))
    components = {
        "recency": config.recency_weight * max(Decimal(0), 1 - age / config.recency_seconds)
        if age is not None
        else None,
        "buy_pressure": config.buy_pressure_weight * buys / (buys + sells)
        if buys is not None and sells is not None and buys + sells
        else None,
        "smart_money": config.smart_money_weight * min(Decimal(100), max(Decimal(0), smart)) / 100
        if smart is not None
        else None,
        "liquidity": config.liquidity_weight
        * min(Decimal(1), max(Decimal(0), liquidity) / config.liquidity_target_usd)
        if liquidity is not None
        else None,
        "risk_adjustment": -config.high_risk_penalty
        if features.get("risk") in {"HIGH", "CRITICAL"}
        else -config.medium_risk_penalty
        if features.get("risk") == "MEDIUM"
        else Decimal(0)
        if features.get("risk") == "LOW"
        else None,
    }
    rounded = {
        k: v.quantize(Decimal("0.01")) if v is not None else None for k, v in components.items()
    }
    score = max(
        Decimal(0),
        min(Decimal(100), sum((v for v in rounded.values() if v is not None), Decimal(0))),
    )
    return {
        "priority_score": str(score),
        "components": {k: str(v) if v is not None else "UNKNOWN" for k, v in rounded.items()},
        "config": config.model_dump(mode="json"),
        "policy": "radar-priority-v1",
    }

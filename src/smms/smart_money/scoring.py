from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SmartMoneyMetrics:
    """Point-in-time wallet metrics; values are rates in [0, 1] unless noted."""

    win_rate: Decimal = Decimal(0)
    avg_roi: Decimal = Decimal(0)
    median_roi: Decimal = Decimal(0)
    risk_adjusted_return: Decimal = Decimal(0)
    early_entry_rate: Decimal = Decimal(0)
    high_multiple_rate: Decimal = Decimal(0)
    ath_capture_rate: Decimal = Decimal(0)
    consistency_rate: Decimal = Decimal(0)
    graduation_rate: Decimal = Decimal(0)
    rug_exposure: Decimal = Decimal(0)
    bot_probability: Decimal = Decimal(0)
    wash_trading_probability: Decimal = Decimal(0)
    sample_size: int = 0
    data_completeness: Decimal = Decimal(1)
    historical_coverage: Decimal = Decimal(1)
    trade_quality: Decimal = Decimal(1)
    time_decay: Decimal = Decimal(1)


@dataclass(frozen=True)
class SmartMoneyScoreWeights:
    win_rate: Decimal = Decimal("0.18")
    roi: Decimal = Decimal("0.14")
    median_roi: Decimal = Decimal("0.10")
    early_entry: Decimal = Decimal("0.18")
    high_multiple: Decimal = Decimal("0.14")
    ath_capture: Decimal = Decimal("0.10")
    consistency: Decimal = Decimal("0.08")
    graduation: Decimal = Decimal("0.04")
    risk_adjusted_return: Decimal = Decimal("0.04")
    rug_penalty: Decimal = Decimal("0.10")
    bot_penalty: Decimal = Decimal("0.10")
    wash_penalty: Decimal = Decimal("0.10")

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any]) -> "SmartMoneyScoreWeights":
        """Build weights from a ``smart_money`` mapping in the scoring config."""

        section = config.get("smart_money", config)
        if not isinstance(section, Mapping):
            raise TypeError("scoring config must contain a smart_money mapping")
        aliases = {"average_roi": "roi"}
        fields = set(cls.__dataclass_fields__)
        values: dict[str, Decimal] = {}
        for key, value in section.items():
            target = aliases.get(str(key), str(key))
            if target in fields:
                values[target] = Decimal(str(value))
        return cls(**values)


@dataclass(frozen=True)
class SmartMoneyNormalization:
    """Saturation scales for unbounded return metrics.

    A positive value equal to its scale contributes 0.5. Larger values keep
    increasing but approach 1 asymptotically, so a single outlier cannot reach
    or exceed the full component weight.
    """

    roi_scale: Decimal = Decimal("0.25")
    median_roi_scale: Decimal = Decimal("0.25")

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any]) -> "SmartMoneyNormalization":
        section = config.get("smart_money_normalization", {})
        if not isinstance(section, Mapping):
            raise TypeError("smart_money_normalization must be a mapping")
        values = {
            key: Decimal(str(value))
            for key, value in section.items()
            if key in cls.__dataclass_fields__
        }
        result = cls(**values)
        if result.roi_scale <= 0 or result.median_roi_scale <= 0:
            raise ValueError("ROI normalization scales must be positive")
        return result


@dataclass(frozen=True)
class SmartMoneyScoreResult:
    score: Decimal
    penalties: Decimal
    components: dict[str, Decimal] = field(default_factory=dict)
    contributions: dict[str, Decimal] = field(default_factory=dict)
    confidence: str = "LOW"
    confidence_score: Decimal = Decimal(0)
    explanation: str = ""


@dataclass(frozen=True)
class TokenSmartMoneySummary:
    token_address: str
    smart_wallet_count: int | None = 0
    smart_money_buy_usd: Decimal | None = Decimal(0)
    smart_money_sell_usd: Decimal | None = Decimal(0)
    smart_money_net_flow: Decimal | None = Decimal(0)
    top_smart_wallets: tuple[str, ...] = ()
    early_smart_buy_count: int | None = 0
    smart_money_score: Decimal | None = None
    data_status: str = "UNKNOWN"

    def to_dict(self) -> dict[str, Any]:
        return {
            "token_address": self.token_address,
            "smart_wallet_count": self.smart_wallet_count,
            "smart_money_buy_usd": str(self.smart_money_buy_usd) if self.smart_money_buy_usd is not None else None,
            "smart_money_sell_usd": str(self.smart_money_sell_usd) if self.smart_money_sell_usd is not None else None,
            "smart_money_net_flow": str(self.smart_money_net_flow) if self.smart_money_net_flow is not None else None,
            "top_smart_wallets": list(self.top_smart_wallets),
            "early_smart_buy_count": self.early_smart_buy_count,
            "smart_money_score": str(self.smart_money_score) if self.smart_money_score is not None else None,
            "data_status": self.data_status,
        }


def aggregate_token_smart_money(token_address: str, wallet_profiles: Mapping[str, Any] | Iterable[Any], trades: Iterable[Any] = (), *, early_buyers: Iterable[Any] = (), min_wallet_score: Decimal = Decimal(60)) -> TokenSmartMoneySummary:
    trade_rows = list(trades)
    participant_wallets = {
        str(
            trade.get("wallet_address", "")
            if isinstance(trade, Mapping)
            else getattr(trade, "wallet_address", "")
        ).lower()
        for trade in trade_rows
        if str(
            trade.get("token_address", "")
            if isinstance(trade, Mapping)
            else getattr(trade, "token_address", "")
        ).lower()
        == token_address.lower()
    }
    profiles = wallet_profiles.items() if isinstance(wallet_profiles, Mapping) else ((getattr(p, "wallet", ""), p) for p in wallet_profiles)
    selected: dict[str, tuple[str, Decimal]] = {}
    for wallet, profile in profiles:
        result = getattr(profile, "smart_money_score_result", None)
        score = getattr(result, "score", None) if result else getattr(profile, "smart_money_score", None)
        if (
            not wallet
            or str(wallet).lower() not in participant_wallets
            or score is None
            or Decimal(str(score)) < min_wallet_score
        ):
            continue
        indicators = getattr(profile, "behavioral_indicators", {}) or {}
        cluster = str(indicators.get("cluster_id") or wallet)
        candidate = (str(wallet).lower(), Decimal(str(score)))
        if cluster not in selected or candidate[1] > selected[cluster][1]:
            selected[cluster] = candidate
    wallets = {w for w, _ in selected.values()}
    early_set = {
        str(
            item.get("wallet_address", item.get("wallet", ""))
            if isinstance(item, Mapping)
            else getattr(item, "wallet_address", "")
        ).lower()
        for item in early_buyers
    }
    buy = sell = Decimal(0)
    missing_buy = missing_sell = False
    early_wallets_seen: set[str] = set()
    for trade in trade_rows:
        if isinstance(trade, Mapping):
            token = trade.get("token_address", "")
            wallet = trade.get("wallet_address", "")
            value = trade.get("usd_value")
            side = trade.get("side", "")
        else:
            token = getattr(trade, "token_address", "")
            wallet = getattr(trade, "wallet_address", "")
            value = getattr(trade, "usd_value", None)
            side = getattr(trade, "side", "")
        if str(token).lower() != token_address.lower() or str(wallet).lower() not in wallets:
            continue
        side = str(getattr(side, "value", side)).upper()
        try:
            amount = Decimal(str(value)) if value is not None else None
            if amount is not None and (not amount.is_finite() or amount < 0):
                amount = None
        except (ArithmeticError, TypeError, ValueError):
            amount = None
        if side == "BUY":
            if amount is None:
                missing_buy = True
            else:
                buy += amount
            if str(wallet).lower() in early_set:
                early_wallets_seen.add(str(wallet).lower())
        elif side == "SELL":
            if amount is None:
                missing_sell = True
            else:
                sell += amount
    ranked = tuple(w for w, _ in sorted(selected.values(), key=lambda x: x[1], reverse=True)[:5])
    avg_score = (sum((s for _, s in selected.values()), Decimal(0)) / Decimal(len(selected))).quantize(Decimal("0.01")) if selected else None
    if not selected:
        return TokenSmartMoneySummary(token_address, None, None, None, None, (), None, None, "UNKNOWN")
    return TokenSmartMoneySummary(token_address, len(selected), None if missing_buy else buy,
        None if missing_sell else sell, None if missing_buy or missing_sell else buy - sell,
        ranked, len(early_wallets_seen), avg_score, "DEGRADED" if missing_buy or missing_sell else "CHAIN_EVENTS")


def exponential_time_decay(age_days: Decimal | float, half_life_days: Decimal | float = 180) -> Decimal:
    """Return a bounded half-life decay factor for historical observations."""
    import math

    age = max(Decimal(0), Decimal(str(age_days)))
    half_life = max(Decimal("0.01"), Decimal(str(half_life_days)))
    return Decimal(str(math.exp(-math.log(2) * float(age / half_life))))


def _clamp(value: Decimal, low: Decimal = Decimal(0), high: Decimal = Decimal(1)) -> Decimal:
    return max(low, min(high, value))


def _normalize_positive_return(value: Decimal, scale: Decimal) -> Decimal:
    positive = max(Decimal(0), value)
    return positive / (positive + scale) if positive else Decimal(0)


def calculate_smart_money_score(
    metrics: SmartMoneyMetrics,
    weights: SmartMoneyScoreWeights | None = None,
    *,
    config_path: str | Path | None = None,
    normalization: SmartMoneyNormalization | None = None,
    age_days: Decimal | float | None = None,
    half_life_days: Decimal | float = 180,
) -> SmartMoneyScoreResult:
    """Calculate an explainable 0-100 score from point-in-time statistics.

    ROI is treated as a decimal return and normalized with a bounded saturation
    curve. Future
    outcome fields such as ATH are accepted only when already materialized in the
    caller's as-of snapshot; this function performs no look-ahead queries.
    """

    if weights is not None and config_path is not None:
        raise ValueError("provide either weights or config_path, not both")
    active = weights or (
        load_smart_money_weights(config_path) if config_path is not None else SmartMoneyScoreWeights()
    )
    normalizer = normalization or (
        load_smart_money_normalization(config_path)
        if config_path is not None
        else SmartMoneyNormalization()
    )
    components = {
        "win_rate": _clamp(metrics.win_rate),
        "roi": _normalize_positive_return(metrics.avg_roi, normalizer.roi_scale),
        "median_roi": _normalize_positive_return(
            metrics.median_roi, normalizer.median_roi_scale
        ),
        "early_entry": _clamp(metrics.early_entry_rate),
        "high_multiple": _clamp(metrics.high_multiple_rate),
        "ath_capture": _clamp(metrics.ath_capture_rate),
        "consistency": _clamp(metrics.consistency_rate),
        "graduation": _clamp(metrics.graduation_rate),
        "risk_adjusted_return": _clamp(metrics.risk_adjusted_return),
    }
    positive = (
        active.win_rate * components["win_rate"]
        + active.roi * components["roi"]
        + active.median_roi * components["median_roi"]
        + active.early_entry * components["early_entry"]
        + active.high_multiple * components["high_multiple"]
        + active.ath_capture * components["ath_capture"]
        + active.consistency * components["consistency"]
        + active.graduation * components["graduation"]
        + active.risk_adjusted_return * components["risk_adjusted_return"]
    )
    penalties = (
        active.rug_penalty * _clamp(metrics.rug_exposure)
        + active.bot_penalty * _clamp(metrics.bot_probability)
        + active.wash_penalty * _clamp(metrics.wash_trading_probability)
    )
    decay = exponential_time_decay(age_days, half_life_days) if age_days is not None else _clamp(metrics.time_decay)
    positive *= decay
    penalties *= decay
    score = _clamp(positive - penalties) * Decimal(100)
    contributions = {
        "win_rate": (active.win_rate * components["win_rate"] * decay * 100).quantize(Decimal("0.01")),
        "roi": (active.roi * components["roi"] * decay * 100).quantize(Decimal("0.01")),
        "median_roi": (active.median_roi * components["median_roi"] * decay * 100).quantize(Decimal("0.01")),
        "early_entry": (active.early_entry * components["early_entry"] * decay * 100).quantize(Decimal("0.01")),
        "high_multiple": (active.high_multiple * components["high_multiple"] * decay * 100).quantize(Decimal("0.01")),
        "ath_capture": (active.ath_capture * components["ath_capture"] * decay * 100).quantize(Decimal("0.01")),
        "consistency": (active.consistency * components["consistency"] * decay * 100).quantize(Decimal("0.01")),
        "graduation": (active.graduation * components["graduation"] * decay * 100).quantize(Decimal("0.01")),
        "risk_adjusted_return": (active.risk_adjusted_return * components["risk_adjusted_return"] * decay * 100).quantize(Decimal("0.01")),
        "rug_penalty": -(active.rug_penalty * _clamp(metrics.rug_exposure) * decay * 100).quantize(Decimal("0.01")),
        "bot_penalty": -(active.bot_penalty * _clamp(metrics.bot_probability) * decay * 100).quantize(Decimal("0.01")),
        "wash_penalty": -(active.wash_penalty * _clamp(metrics.wash_trading_probability) * decay * 100).quantize(Decimal("0.01")),
    }
    sample_factor = min(Decimal(1), Decimal(metrics.sample_size) / Decimal(20)) if metrics.sample_size else Decimal(0)
    confidence_score = (sample_factor * _clamp(metrics.data_completeness) * _clamp(metrics.historical_coverage) * _clamp(metrics.trade_quality) * 100).quantize(Decimal("0.01"))
    confidence = "HIGH" if confidence_score >= 70 else ("MEDIUM" if confidence_score >= 40 else "LOW")
    explanation = (
        "Weighted historical win rate, average and median ROI, entry timing, multiple hits, "
        "ATH capture, consistency, graduation, and risk-adjusted return minus rug, bot, "
        "and wash-trading penalties."
    )
    return SmartMoneyScoreResult(
        score=score.quantize(Decimal("0.01")),
        penalties=(penalties * Decimal(100)).quantize(Decimal("0.01")),
        components=components,
        contributions=contributions,
        confidence=confidence,
        confidence_score=confidence_score,
        explanation=explanation,
    )


def load_smart_money_weights(path: str | Path = "configs/scoring.yaml") -> SmartMoneyScoreWeights:
    """Load the configured score weights from YAML.

    PyYAML is an explicit runtime dependency so configuration remains structured
    rather than being parsed with ad-hoc string rules.
    """

    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - exercised in minimal installs
        raise RuntimeError("PyYAML is required to load scoring configuration") from exc
    with Path(path).open(encoding="utf-8") as stream:
        parsed = yaml.safe_load(stream) or {}
    if not isinstance(parsed, Mapping):
        raise TypeError("scoring config root must be a mapping")
    return SmartMoneyScoreWeights.from_mapping(parsed)


def load_smart_money_normalization(
    path: str | Path = "configs/scoring.yaml",
) -> SmartMoneyNormalization:
    import yaml

    with Path(path).open(encoding="utf-8") as stream:
        parsed = yaml.safe_load(stream) or {}
    if not isinstance(parsed, Mapping):
        raise TypeError("scoring config root must be a mapping")
    return SmartMoneyNormalization.from_mapping(parsed)

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass(frozen=True)
class SmartMoneyMetrics:
    """Point-in-time wallet metrics; values are rates in [0, 1] unless noted."""

    win_rate: Decimal = Decimal(0)
    avg_roi: Decimal = Decimal(0)
    median_roi: Decimal = Decimal(0)
    early_entry_rate: Decimal = Decimal(0)
    high_multiple_rate: Decimal = Decimal(0)
    ath_capture_rate: Decimal = Decimal(0)
    consistency_rate: Decimal = Decimal(0)
    graduation_rate: Decimal = Decimal(0)
    rug_exposure: Decimal = Decimal(0)
    bot_probability: Decimal = Decimal(0)
    wash_trading_probability: Decimal = Decimal(0)


@dataclass(frozen=True)
class SmartMoneyScoreWeights:
    win_rate: Decimal = Decimal("0.20")
    roi: Decimal = Decimal("0.20")
    early_entry: Decimal = Decimal("0.20")
    high_multiple: Decimal = Decimal("0.15")
    ath_capture: Decimal = Decimal("0.10")
    consistency: Decimal = Decimal("0.10")
    graduation: Decimal = Decimal("0.05")
    rug_penalty: Decimal = Decimal("0.10")
    bot_penalty: Decimal = Decimal("0.10")
    wash_penalty: Decimal = Decimal("0.10")


@dataclass(frozen=True)
class SmartMoneyScoreResult:
    score: Decimal
    penalties: Decimal
    components: dict[str, Decimal] = field(default_factory=dict)
    explanation: str = ""


def _clamp(value: Decimal, low: Decimal = Decimal(0), high: Decimal = Decimal(1)) -> Decimal:
    return max(low, min(high, value))


def calculate_smart_money_score(
    metrics: SmartMoneyMetrics,
    weights: SmartMoneyScoreWeights | None = None,
) -> SmartMoneyScoreResult:
    """Calculate an explainable 0-100 score from point-in-time statistics.

    ROI is normalized conservatively to a 0..1 contribution at 100% ROI. Future
    outcome fields such as ATH are accepted only when already materialized in the
    caller's as-of snapshot; this function performs no look-ahead queries.
    """

    active = weights or SmartMoneyScoreWeights()
    components = {
        "win_rate": _clamp(metrics.win_rate),
        "roi": _clamp(metrics.avg_roi / Decimal(2)),
        "early_entry": _clamp(metrics.early_entry_rate),
        "high_multiple": _clamp(metrics.high_multiple_rate),
        "ath_capture": _clamp(metrics.ath_capture_rate),
        "consistency": _clamp(metrics.consistency_rate),
        "graduation": _clamp(metrics.graduation_rate),
    }
    positive = (
        active.win_rate * components["win_rate"]
        + active.roi * components["roi"]
        + active.early_entry * components["early_entry"]
        + active.high_multiple * components["high_multiple"]
        + active.ath_capture * components["ath_capture"]
        + active.consistency * components["consistency"]
        + active.graduation * components["graduation"]
    )
    penalties = (
        active.rug_penalty * _clamp(metrics.rug_exposure)
        + active.bot_penalty * _clamp(metrics.bot_probability)
        + active.wash_penalty * _clamp(metrics.wash_trading_probability)
    )
    score = _clamp(positive - penalties) * Decimal(100)
    explanation = (
        "Weighted historical win rate, ROI, entry timing, multiple hits, ATH capture, "
        "consistency, and graduation participation minus rug, bot, and wash-trading penalties."
    )
    return SmartMoneyScoreResult(
        score=score.quantize(Decimal("0.01")),
        penalties=(penalties * Decimal(100)).quantize(Decimal("0.01")),
        components=components,
        explanation=explanation,
    )


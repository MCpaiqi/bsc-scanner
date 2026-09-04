from collections.abc import Mapping
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
    *,
    config_path: str | Path | None = None,
) -> SmartMoneyScoreResult:
    """Calculate an explainable 0-100 score from point-in-time statistics.

    ROI is treated as a decimal return and clamped to a 0..1 contribution. Future
    outcome fields such as ATH are accepted only when already materialized in the
    caller's as-of snapshot; this function performs no look-ahead queries.
    """

    if weights is not None and config_path is not None:
        raise ValueError("provide either weights or config_path, not both")
    active = weights or (
        load_smart_money_weights(config_path) if config_path is not None else SmartMoneyScoreWeights()
    )
    components = {
        "win_rate": _clamp(metrics.win_rate),
        "roi": _clamp(metrics.avg_roi),
        "median_roi": _clamp(metrics.median_roi),
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
    score = _clamp(positive - penalties) * Decimal(100)
    explanation = (
        "Weighted historical win rate, average and median ROI, entry timing, multiple hits, "
        "ATH capture, consistency, graduation, and risk-adjusted return minus rug, bot, "
        "and wash-trading penalties."
    )
    return SmartMoneyScoreResult(
        score=score.quantize(Decimal("0.01")),
        penalties=(penalties * Decimal(100)).quantize(Decimal("0.01")),
        components=components,
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

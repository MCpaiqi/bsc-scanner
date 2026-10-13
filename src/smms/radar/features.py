"""Canonical feature contract shared by radar, audit, signals, and the API."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any, ClassVar

UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class RadarFeatureSnapshot:
    age_seconds: int
    market_phase: str
    liquidity: Any
    market_cap: Any
    price: Any
    volume_1m: Any
    volume_5m: Any
    early_buyer_count: Any
    known_smart_wallet_count: Any
    smart_money_net_flow: Any
    smart_money_score: Any
    risk: Any
    buy_count_1m: Any
    sell_count_1m: Any
    buy_sell_ratio_1m: Any
    unique_buyers_1m: Any
    unique_sellers_1m: Any
    buy_count_5m: Any
    sell_count_5m: Any
    buy_sell_ratio_5m: Any
    unique_buyers_5m: Any
    unique_sellers_5m: Any
    reason_codes: Any = None
    feature_version: str = "radar-features-v2"

    REQUIRED_KEYS: ClassVar[frozenset[str]] = frozenset(
        name for name in __annotations__ if name not in {"REQUIRED_KEYS"}
    )

    @classmethod
    def unknown(cls, *, age_seconds: int, market_phase: str, **values: Any):
        payload = {
            name: UNKNOWN
            for name in cls.REQUIRED_KEYS
            if name not in {"age_seconds", "market_phase", "feature_version"}
        }
        payload.update(
            age_seconds=age_seconds,
            market_phase=market_phase,
            feature_version="radar-features-v2",
        )
        payload.update(values)
        return cls(**payload)

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]):
        missing = cls.REQUIRED_KEYS - values.keys()
        if missing:
            raise ValueError(f"missing radar feature keys: {', '.join(sorted(missing))}")
        return cls(**{name: values[name] for name in cls.REQUIRED_KEYS})

    @property
    def smart_money_entry(self) -> bool:
        value = self.known_smart_wallet_count
        return value not in {None, UNKNOWN} and int(value) > 0

    @property
    def risk_deteriorated(self) -> bool:
        return str(self.risk).upper() in {"HIGH", "CRITICAL"}

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


__all__ = ["UNKNOWN", "RadarFeatureSnapshot"]

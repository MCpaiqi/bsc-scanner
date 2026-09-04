from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal


@dataclass(frozen=True)
class HistoricalTokenSnapshot:
    address: str
    symbol: str | None
    first_seen_at: datetime
    ath_multiple: Decimal | None
    outcome_class: str = "UNKNOWN"


@dataclass(frozen=True)
class HistoricalWinnerConfig:
    min_multiple: Decimal = Decimal(50)
    preferred_multiple: Decimal = Decimal(100)
    lookback_days: int = 365


def discover_historical_winners(
    snapshots: Iterable[HistoricalTokenSnapshot],
    *,
    as_of: datetime,
    config: HistoricalWinnerConfig | None = None,
) -> list[HistoricalTokenSnapshot]:
    """Return only historical samples observable by ``as_of`` and above the threshold."""

    active = config or HistoricalWinnerConfig()
    cutoff = as_of.astimezone(UTC) - timedelta(days=active.lookback_days)
    observed: list[HistoricalTokenSnapshot] = []
    for token in snapshots:
        first_seen = token.first_seen_at.astimezone(UTC)
        multiple = token.ath_multiple
        if (
            cutoff <= first_seen <= as_of.astimezone(UTC)
            and multiple is not None
            and multiple >= active.min_multiple
        ):
            observed.append(token)
    return sorted(observed, key=lambda item: (item.first_seen_at, item.address))


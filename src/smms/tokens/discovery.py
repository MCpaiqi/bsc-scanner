from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from smms.smart_money.historical import HistoricalTokenSnapshot, HistoricalWinnerConfig


@dataclass(frozen=True)
class WinnerDiscoveryResult:
    as_of: datetime
    config: HistoricalWinnerConfig
    winners: tuple[HistoricalTokenSnapshot, ...]


def discover_winners(
    snapshots: Iterable[HistoricalTokenSnapshot],
    *,
    as_of: datetime,
    min_multiple: Decimal = Decimal(50),
    preferred_multiple: Decimal = Decimal(100),
    lookback_days: int = 365,
) -> WinnerDiscoveryResult:
    config = HistoricalWinnerConfig(
        min_multiple=min_multiple,
        preferred_multiple=preferred_multiple,
        lookback_days=lookback_days,
    )
    from smms.smart_money.historical import discover_historical_winners

    winners = discover_historical_winners(snapshots, as_of=as_of, config=config)
    return WinnerDiscoveryResult(as_of=as_of.astimezone(UTC), config=config, winners=tuple(winners))


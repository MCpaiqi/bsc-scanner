"""Shared, versioned evaluation of observed price paths in one price basis."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

PATH_EVALUATION_VERSION = "price-path-v2"
FORWARD_EVALUATION_VERSION = "forward-v2"
ENDPOINT_TOLERANCE = timedelta(seconds=5)


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def positive_price(value: Any) -> Decimal | None:
    try:
        price = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return price if price.is_finite() and price > 0 else None


@dataclass(frozen=True)
class PricePoint:
    timestamp: datetime
    price: Decimal
    reference: str = ""
    provenance: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class PathEvaluation:
    endpoint: PricePoint
    endpoint_offset_seconds: float
    path: tuple[PricePoint, ...]
    returns: tuple[Decimal, ...]
    return_pct: Decimal
    high_return: Decimal
    low_return: Decimal
    max_drawdown: Decimal


def evaluate_price_path(
    entry_price: Decimal,
    observed_at: datetime,
    prices: Iterable[PricePoint],
    *,
    horizon: timedelta,
    tolerance: timedelta = ENDPOINT_TOLERANCE,
    at: datetime | None = None,
) -> PathEvaluation | None:
    """Use the nearest bounded endpoint and its actual observed path.

    Extrema describe samples, not an uninterrupted coverage claim. Entry belongs
    to the path so a loss on its very first subsequent point is still a drawdown.
    Equal-distance endpoints prefer the earlier timestamp, then stable reference.
    """
    if tolerance < timedelta(0) or horizon <= timedelta(0):
        raise ValueError("horizon must be positive and tolerance non-negative")
    entry = positive_price(entry_price)
    start = utc(observed_at)
    target = start + horizon
    cutoff = utc(at) if at is not None else target + tolerance
    if entry is None or cutoff < target:
        return None
    points = sorted((
        replace(point, timestamp=utc(point.timestamp), price=price)
        for point in prices
        if (price := positive_price(point.price)) is not None
        and start < utc(point.timestamp) <= min(cutoff, target + tolerance)
    ), key=lambda point: (point.timestamp, point.reference))
    endpoints = [point for point in points if abs(point.timestamp - target) <= tolerance]
    if not endpoints:
        return None
    endpoint = min(endpoints, key=lambda point: (
        abs(point.timestamp - target), point.timestamp, point.reference,
    ))
    path = (PricePoint(start, entry, "ENTRY"), *(
        point for point in points
        if (point.timestamp, point.reference) <= (endpoint.timestamp, endpoint.reference)
    ))
    returns = tuple(point.price / entry - 1 for point in path)
    peak, drawdown = entry, Decimal(0)
    for point in path:
        peak = max(peak, point.price)
        drawdown = min(drawdown, point.price / peak - 1)
    return PathEvaluation(
        endpoint=endpoint, endpoint_offset_seconds=(endpoint.timestamp - target).total_seconds(),
        path=path, returns=returns, return_pct=endpoint.price / entry - 1,
        high_return=max(returns), low_return=min(returns), max_drawdown=drawdown,
    )

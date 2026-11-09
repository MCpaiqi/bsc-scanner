"""Exact execution-window totals, with one sliding sweep after timestamp sorting."""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal

from smms.evaluation import utc

VWAP_ARITHMETIC = "exact-scaled-integer-v1"


@dataclass(frozen=True)
class WindowPoint:
    timestamp: datetime
    quote: Decimal | None = None
    token: Decimal | None = None
    notional: Decimal | None = None


class _ExactSum:
    def __init__(self):
        self.units = 0
        self.scale = None

    def add(self, value, sign=1):
        parts = value.as_tuple()
        coefficient = int("".join(map(str, parts.digits))) * (-1 if parts.sign else 1)
        if self.scale is None:
            self.scale = parts.exponent
        elif parts.exponent < self.scale:
            self.units *= 10 ** (self.scale - parts.exponent)
            self.scale = parts.exponent
        self.units += sign * coefficient * 10 ** (parts.exponent - self.scale)

    @property
    def value(self):
        if self.scale is None:
            return Decimal(0)
        parts = Decimal(self.units).as_tuple()
        return Decimal((parts.sign, parts.digits, self.scale))


class _Window:
    def __init__(self):
        self.quote, self.token, self.notional = _ExactSum(), _ExactSum(), _ExactSum()

    def add(self, point, sign=1):
        if point.quote is not None:
            self.quote.add(point.quote, sign)
            self.token.add(point.token, sign)
            self.notional.add(point.notional, sign)

    def price(self, minimum):
        if self.token.value <= 0 or self.notional.value < minimum:
            return None
        return self.quote.value / self.token.value


def window_vwap(points, *, start, end, minimum):
    total = _Window()
    start, end = utc(start), utc(end)
    for point in points:
        if start < utc(point.timestamp) <= end:
            total.add(point)
    return total.price(minimum)


def best_window_vwap(points, *, start, end, minimum):
    start, end = utc(start), utc(end)
    ordered = sorted((replace(point, timestamp=utc(point.timestamp)) for point in points
                      if start < utc(point.timestamp) <= end), key=lambda point: point.timestamp)
    anchors = sorted({point.timestamp for point in ordered})
    for width in (30, 60):
        total, left, right, best = _Window(), 0, 0, None
        for anchor in anchors:
            # Every event at the inclusive endpoint enters before it is tested.
            while right < len(ordered) and ordered[right].timestamp <= anchor:
                total.add(ordered[right])
                right += 1
            boundary = max(start, anchor - timedelta(seconds=width))
            while left < right and ordered[left].timestamp <= boundary:
                total.add(ordered[left], -1)
                left += 1
            value = total.price(minimum)
            if value is not None and (best is None or value > best[0]):
                best = value, anchor
        if best is not None:
            return best
    return None, None

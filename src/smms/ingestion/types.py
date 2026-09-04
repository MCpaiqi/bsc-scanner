from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal, TypedDict


class RawToken(TypedDict, total=False):
    address: str
    symbol: str
    name: str
    decimals: int
    chain: str
    metadata: dict[str, Any]


class RawTransaction(TypedDict, total=False):
    signature: str
    slot: int
    block_time: int | None
    wallet_address: str
    token_in: str | None
    token_out: str | None
    amount_in_raw: int | str | None
    amount_out_raw: int | str | None
    decimals_in: int | None
    decimals_out: int | None
    side: Literal["BUY", "SELL", "TRANSFER", "UNKNOWN"]
    dex: str | None
    metadata: dict[str, Any]


class RawSwap(TypedDict, total=False):
    signature: str
    wallet_address: str
    timestamp: int | float | str | datetime
    token_in: str | None
    token_out: str | None
    amount_in_raw: int | str | None
    amount_out_raw: int | str | None
    decimals_in: int | None
    decimals_out: int | None
    usd_value: int | float | str | Decimal | None
    dex: str | None
    metadata: dict[str, Any]


def utc_from_unix(value: float | str | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=UTC)
    except (TypeError, ValueError, OverflowError):
        return None


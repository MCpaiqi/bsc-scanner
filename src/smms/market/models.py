"""Immutable records emitted by the BSC market decoder."""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any


class TradeSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class TokenRecord:
    chain_id: int
    address: str
    symbol: str | None = None
    decimals: int | None = None
    creator: str | None = None
    creation_tx_hash: str | None = None
    creation_block: int | None = None
    creation_timestamp: datetime | None = None


@dataclass(frozen=True)
class PairRecord:
    chain_id: int
    address: str
    token0: str
    token1: str
    factory: str | None
    dex: str
    quote_token: str | None
    creation_tx_hash: str
    creation_block: int
    creation_timestamp: datetime | None
    creation_block_hash: str | None = None


@dataclass(frozen=True)
class MarketEventRecord:
    event_type: str
    chain_id: int
    token: str | None
    pair: str | None
    wallet: str | None
    tx_hash: str
    block: int | None
    timestamp: datetime | None = None
    side: TradeSide | None = None
    token_amount: Decimal | None = None
    token_decimals: int | None = None
    quote_amount: Decimal | None = None
    quote_decimals: int | None = None
    native_amount: Decimal | None = None
    price_quote: Decimal | None = None
    value_quote: Decimal | None = None
    price_usd: Decimal | None = None
    usd_value: Decimal | None = None
    price: Decimal | None = None
    source: str = "bsc:unknown"
    confidence: Decimal | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

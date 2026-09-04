from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class ChainLog:
    """A provider-normalized log while retaining its raw payload."""

    address: str
    topics: tuple[str, ...]
    data: str
    block_number: int
    transaction_hash: str
    log_index: int
    transaction_index: int | None = None
    timestamp: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TransactionReceipt:
    transaction_hash: str
    block_number: int
    status: int | None
    logs: tuple[ChainLog, ...] = ()
    gas_used: Decimal | None = None
    contract_address: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ChainTransaction:
    transaction_hash: str
    block_number: int | None
    from_address: str
    to_address: str | None
    value: Decimal | None = None
    input_data: str = "0x"
    timestamp: datetime | None = None
    receipt: TransactionReceipt | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Block:
    number: int
    block_hash: str
    timestamp: datetime
    transactions: tuple[ChainTransaction, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict)


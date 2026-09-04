from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any


class BondingCurveStatus(str, Enum):
    NEW = "NEW"
    TRADING = "TRADING"
    NEAR_GRADUATION = "NEAR_GRADUATION"
    GRADUATED = "GRADUATED"
    DEX = "DEX"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class LaunchpadTokenDiscovery:
    chain: str
    launchpad: str
    token_address: str
    creator_address: str | None
    created_at: datetime
    creation_tx_hash: str | None = None
    creation_block: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BondingCurveState:
    status: BondingCurveStatus
    price: Decimal | None = None
    liquidity: Decimal | None = None
    progress: Decimal | None = None
    reserve_native: Decimal | None = None
    reserve_token: Decimal | None = None
    as_of_block: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


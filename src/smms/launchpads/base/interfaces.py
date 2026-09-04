from collections.abc import AsyncIterator, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from smms.chains.base import ChainTransaction, TransactionReceipt
from smms.db.models import Token, TokenLifecycleEvent, TradeEvent

from .models import BondingCurveState, LaunchpadTokenDiscovery


@runtime_checkable
class TokenDiscoveryAdapter(Protocol):
    async def discover_tokens(
        self,
        *,
        from_block: int | None = None,
        to_block: int | None = None,
        live: bool = False,
    ) -> AsyncIterator[LaunchpadTokenDiscovery]: ...


@runtime_checkable
class LaunchpadTradeDecoder(Protocol):
    def decode_trades(
        self,
        transaction: ChainTransaction,
        receipt: TransactionReceipt,
        *,
        timestamp: datetime | None = None,
    ) -> Sequence[TradeEvent]: ...


@runtime_checkable
class LaunchpadLifecycleDecoder(Protocol):
    def decode_creation(
        self,
        transaction: ChainTransaction,
        receipt: TransactionReceipt,
        *,
        timestamp: datetime | None = None,
    ) -> Token | TokenLifecycleEvent | None: ...

    def decode_lifecycle(
        self,
        transaction: ChainTransaction,
        receipt: TransactionReceipt,
        *,
        timestamp: datetime | None = None,
    ) -> Sequence[TokenLifecycleEvent]: ...


@runtime_checkable
class BondingCurveAdapter(Protocol):
    async def get_price(self, token_address: str, *, block_number: int | None = None) -> Decimal | None: ...

    async def get_quote(
        self,
        token_address: str,
        amount: Decimal,
        *,
        side: str,
        block_number: int | None = None,
    ) -> Decimal | None: ...

    async def get_liquidity(self, token_address: str, *, block_number: int | None = None) -> Decimal | None: ...

    async def get_progress(self, token_address: str, *, block_number: int | None = None) -> Decimal | None: ...

    async def get_state(self, token_address: str, *, block_number: int | None = None) -> BondingCurveState: ...


@runtime_checkable
class LaunchpadAdapter(
    TokenDiscoveryAdapter,
    LaunchpadTradeDecoder,
    LaunchpadLifecycleDecoder,
    BondingCurveAdapter,
    Protocol,
):
    chain: str
    launchpad: str

"""Composed Flap launchpad adapter."""

from collections.abc import AsyncIterator, Sequence
from datetime import datetime
from decimal import Decimal

from smms.chains.base import ChainProvider, ChainTransaction, TransactionReceipt
from smms.db.models import Token, TokenLifecycleEvent, TradeEvent
from smms.launchpads.base import BondingCurveState, LaunchpadTokenDiscovery

from .bonding_curve import FlapBondingCurveAdapter, QuoteReader, StateReader
from .config import FlapConfig
from .discovery import FlapTokenDiscovery
from .lifecycle import FlapLifecycleDecoder
from .trade_decoder import FlapTradeDecoder


class FlapAdapter:
    """Expose all Flap capabilities through the chain-neutral adapter seam."""

    chain = "bsc"
    launchpad = "FLAP"

    def __init__(
        self,
        provider: ChainProvider,
        config: FlapConfig,
        *,
        state_reader: StateReader | None = None,
        quote_reader: QuoteReader | None = None,
    ) -> None:
        self.provider = provider
        self.config = config
        self.chain = config.chain
        self.launchpad = config.launchpad
        self.discovery = FlapTokenDiscovery(provider, config)
        self.trade_decoder = FlapTradeDecoder(config)
        self.lifecycle_decoder = FlapLifecycleDecoder(config)
        self.bonding_curve = FlapBondingCurveAdapter(
            state_reader=state_reader,
            quote_reader=quote_reader,
        )

    async def discover_tokens(
        self,
        *,
        from_block: int | None = None,
        to_block: int | None = None,
        live: bool = False,
    ) -> AsyncIterator[LaunchpadTokenDiscovery]:
        async for record in self.discovery.discover_tokens(
            from_block=from_block,
            to_block=to_block,
            live=live,
        ):
            yield record

    def decode_creation(
        self,
        transaction: ChainTransaction,
        receipt: TransactionReceipt,
        *,
        timestamp: datetime | None = None,
    ) -> Token | TokenLifecycleEvent | None:
        return self.lifecycle_decoder.decode_creation(
            transaction,
            receipt,
            timestamp=timestamp,
        )

    def decode_trades(
        self,
        transaction: ChainTransaction,
        receipt: TransactionReceipt,
        *,
        timestamp: datetime | None = None,
    ) -> Sequence[TradeEvent]:
        return self.trade_decoder.decode_trades(
            transaction,
            receipt,
            timestamp=timestamp,
        )

    def decode_lifecycle(
        self,
        transaction: ChainTransaction,
        receipt: TransactionReceipt,
        *,
        timestamp: datetime | None = None,
    ) -> Sequence[TokenLifecycleEvent]:
        return self.lifecycle_decoder.decode_lifecycle(
            transaction,
            receipt,
            timestamp=timestamp,
        )

    async def decode_transaction(
        self,
        transaction: ChainTransaction,
        receipt: TransactionReceipt | None = None,
    ) -> tuple[Sequence[TradeEvent], Sequence[TokenLifecycleEvent]]:
        """Decode an RPC transaction, resolving its block timestamp when needed."""

        resolved_receipt = receipt or transaction.receipt
        if resolved_receipt is None:
            resolved_receipt = await self.provider.get_transaction_receipt(
                transaction.transaction_hash
            )
        if resolved_receipt is None:
            raise ValueError("transaction receipt is unavailable")

        timestamp = transaction.timestamp
        if timestamp is None:
            block_number = (
                transaction.block_number
                if transaction.block_number is not None
                else resolved_receipt.block_number
            )
            get_block_timestamp = getattr(self.provider, "get_block_timestamp", None)
            if callable(get_block_timestamp):
                timestamp = await get_block_timestamp(block_number)
            else:
                block = await self.provider.get_block(block_number, include_transactions=False)
                timestamp = block.timestamp if block is not None else None

        return (
            self.decode_trades(transaction, resolved_receipt, timestamp=timestamp),
            self.decode_lifecycle(transaction, resolved_receipt, timestamp=timestamp),
        )

    async def get_price(self, token_address: str, *, block_number: int | None = None) -> Decimal | None:
        return await self.bonding_curve.get_price(token_address, block_number=block_number)

    async def get_quote(
        self,
        token_address: str,
        amount: Decimal,
        *,
        side: str,
        block_number: int | None = None,
    ) -> Decimal | None:
        return await self.bonding_curve.get_quote(
            token_address,
            amount,
            side=side,
            block_number=block_number,
        )

    async def get_liquidity(
        self, token_address: str, *, block_number: int | None = None
    ) -> Decimal | None:
        return await self.bonding_curve.get_liquidity(token_address, block_number=block_number)

    async def get_progress(
        self, token_address: str, *, block_number: int | None = None
    ) -> Decimal | None:
        return await self.bonding_curve.get_progress(token_address, block_number=block_number)

    async def get_state(
        self, token_address: str, *, block_number: int | None = None
    ) -> BondingCurveState:
        return await self.bonding_curve.get_state(token_address, block_number=block_number)


FlapLaunchpadAdapter = FlapAdapter


__all__ = ["FlapAdapter", "FlapLaunchpadAdapter"]

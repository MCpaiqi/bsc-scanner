"""Async idempotent repositories for canonical EVM events."""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from smms.db.models import TokenLifecycleEvent, TradeEvent, WalletTokenPerformance


class AsyncTradeEventRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_identity(
        self, chain: str, tx_hash: str, event_index: int
    ) -> TradeEvent | None:
        return await self.session.scalar(
            select(TradeEvent).where(
                TradeEvent.chain == chain,
                TradeEvent.tx_hash == tx_hash,
                TradeEvent.event_index == event_index,
            )
        )

    async def add_idempotent(self, event: TradeEvent) -> TradeEvent:
        existing = await self.get_by_identity(event.chain, event.tx_hash, event.event_index)
        if existing is None:
            try:
                async with self.session.begin_nested():
                    self.session.add(event)
                    await self.session.flush()
                existing = event
            except IntegrityError:
                existing = await self.get_by_identity(event.chain, event.tx_hash, event.event_index)
                if existing is None:
                    raise
        if existing is not event:
            self._merge(existing, event)
            await self.session.flush()
        return existing

    @staticmethod
    def _merge(target: TradeEvent, source: TradeEvent) -> None:
        for name in (
            "launchpad",
            "token_address",
            "trader_address",
            "block_number",
            "timestamp",
            "side",
            "token_amount",
            "native_amount",
            "price",
            "market_cap",
            "liquidity",
            "source",
            "metadata_json",
        ):
            value = getattr(source, name)
            if value is not None:
                setattr(target, name, value)


class AsyncTokenLifecycleEventRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_identity(
        self, chain: str, tx_hash: str, event_index: int
    ) -> TokenLifecycleEvent | None:
        return await self.session.scalar(
            select(TokenLifecycleEvent).where(
                TokenLifecycleEvent.chain == chain,
                TokenLifecycleEvent.tx_hash == tx_hash,
                TokenLifecycleEvent.event_index == event_index,
            )
        )

    async def add_idempotent(self, event: TokenLifecycleEvent) -> TokenLifecycleEvent:
        if not event.tx_hash:
            raise ValueError("lifecycle event tx_hash is required for idempotency")
        existing = await self.get_by_identity(event.chain, event.tx_hash, event.event_index)
        if existing is None:
            try:
                async with self.session.begin_nested():
                    self.session.add(event)
                    await self.session.flush()
                existing = event
            except IntegrityError:
                existing = await self.get_by_identity(event.chain, event.tx_hash, event.event_index)
                if existing is None:
                    raise
        if existing is not event:
            for name in (
                "launchpad",
                "token_address",
                "event_type",
                "timestamp",
                "block_number",
                "payload_json",
            ):
                value = getattr(event, name)
                if value is not None:
                    setattr(existing, name, value)
            await self.session.flush()
        return existing


class AsyncWalletTokenPerformanceRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_pair(
        self, chain: str, wallet_address: str, token_address: str
    ) -> WalletTokenPerformance | None:
        return await self.session.scalar(
            select(WalletTokenPerformance).where(
                WalletTokenPerformance.chain == chain,
                WalletTokenPerformance.wallet_address == wallet_address,
                WalletTokenPerformance.token_address == token_address,
            )
        )

    async def upsert(self, performance: WalletTokenPerformance) -> WalletTokenPerformance:
        existing = await self.get_by_pair(
            performance.chain,
            performance.wallet_address,
            performance.token_address,
        )
        if existing is None:
            try:
                async with self.session.begin_nested():
                    self.session.add(performance)
                    await self.session.flush()
                return performance
            except IntegrityError:
                existing = await self.get_by_pair(
                    performance.chain,
                    performance.wallet_address,
                    performance.token_address,
                )
                if existing is None:
                    raise
        for name in (
            "launchpad",
            "entry_time",
            "entry_market_cap",
            "entry_price",
            "position_size",
            "total_buy",
            "total_sell",
            "realized_pnl",
            "unrealized_pnl",
            "max_pnl",
            "max_drawdown",
            "exit_time",
            "holding_duration",
            "holding_duration_seconds",
            "roi",
            "multiple",
            "reached_2x",
            "reached_5x",
            "reached_10x",
            "reached_50x",
            "reached_100x",
        ):
            value = getattr(performance, name)
            if value is not None:
                setattr(existing, name, value)
        await self.session.flush()
        return existing

"""Regression coverage for live BSC token metadata persistence boundaries."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from smms.chains.base import ChainTransaction
from smms.chains.bsc.transactions import TransactionContextResolver
from smms.config.settings import Settings
from smms.db.models import Base, Pair, RawChainEvent, Token, TokenMarketSnapshot, TradeEvent
from smms.market.metadata import _selector
from smms.market.models import MarketEventRecord, TradeSide
from smms.market.pipeline import BSCMarketPipeline
from smms.market.protocols import PancakeSwapConfig

LONG_SYMBOL = (
    "ThisIsTheLongestTokenTickerStoryEverCreatedInCryptoHistoryAndIfYouAreReadingThis"
    "YouHaveJustEnteredAStoryThatWasNeverPlannedToEnd"
)


def _database_url() -> str:
    configured = os.environ.get("BSC_REGRESSION_DATABASE_URL")
    if configured:
        return configured
    return Settings(_env_file=".env").database_url


def _dynamic_string(value: str) -> str:
    encoded = value.encode("utf-8")
    padded = encoded.hex().ljust(((len(encoded) + 31) // 32) * 64, "0")
    return "0x" + f"{32:064x}" + f"{len(encoded):064x}" + padded


class _MetadataProvider:
    async def eth_call(self, _address: str, selector: str) -> str:
        if selector == _selector("symbol()"):
            return _dynamic_string(LONG_SYMBOL)
        if selector == _selector("decimals()"):
            return "0x" + f"{18:064x}"
        return "0x"


class _RawEvent:
    timestamp = datetime(2026, 9, 15, tzinfo=UTC)


@pytest.mark.asyncio
async def test_live_token_metadata_overlong_symbol_is_bounded_before_flush() -> None:
    """A real token metadata response must not abort the swap transaction."""
    url = _database_url()
    if "+asyncpg" not in url:
        url = url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)

    engine = create_async_engine(url, pool_pre_ping=True)
    try:
        try:
            async with engine.connect() as connection:
                await connection.exec_driver_sql("SELECT 1")
        except Exception as exc:  # noqa: BLE001 - local integration DB is optional
            pytest.skip(f"local PostgreSQL unavailable: {type(exc).__name__}")

        address = "0x" + uuid.uuid4().hex + "00000000"
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            token = await BSCMarketPipeline(
                session,
                metadata_provider=_MetadataProvider(),
            )._persist_token(address, _RawEvent())

            assert token.symbol == LONG_SYMBOL[:32]
            assert token.metadata_json["symbol"] == LONG_SYMBOL
            assert token.decimals == 18
            await session.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_complete_existing_token_does_not_issue_metadata_rpc_per_log() -> None:
    """Catch-up must not re-fetch metadata for every swap of a known token."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    address = "0x" + "7" * 40

    class Provider:
        calls = 0

        async def eth_call(self, _address: str, _selector: str) -> str:
            self.calls += 1
            return "0x"

    provider = Provider()
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            session.add(Token(chain="bsc", address=address, symbol="KNOWN", name="Known", decimals=18))
            await session.flush()
            token = await BSCMarketPipeline(session, metadata_provider=provider)._persist_token(address, _RawEvent())
            assert token.symbol == "KNOWN"
            assert provider.calls == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_live_swap_raw_amounts_fit_market_event_storage() -> None:
    """Raw uint amounts from a high-supply token must persist without overflow."""
    url = _database_url()
    if "+asyncpg" not in url:
        url = url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)

    engine = create_async_engine(url, pool_pre_ping=True)
    try:
        try:
            async with engine.connect() as connection:
                await connection.exec_driver_sql("SELECT 1")
        except Exception as exc:  # noqa: BLE001 - local PostgreSQL is optional
            pytest.skip(f"local PostgreSQL unavailable: {type(exc).__name__}")

        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            record = MarketEventRecord(
                event_type="TRADE",
                chain_id=56,
                token="0x" + "1" * 40,
                pair="0x" + "2" * 40,
                wallet="0x" + "3" * 40,
                tx_hash="0xraw-amount-" + uuid.uuid4().hex,
                block=121727374,
                timestamp=datetime(2026, 9, 15, tzinfo=UTC),
                side=TradeSide.BUY,
                token_amount=10**24,
                quote_amount=2 * 10**18,
                source="bsc:pancakeswap",
                confidence=1,
                metadata={"log_index": 171, "block_hash": "0x" + "4" * 64},
            )
            event = await BSCMarketPipeline(session)._persist_event(record)
            assert event.token_amount == 10**24
            assert event.quote_amount == 2 * 10**18
            await session.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_swap_enriches_existing_quote_token_before_trade_conversion() -> None:
    """An existing token with missing decimals must use the chain metadata call."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    pair_address = "0x" + "a" * 40
    quote_address = "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"
    token_address = "0x" + "b" * 40
    user_address = "0x" + "c" * 40
    config = PancakeSwapConfig()

    class Provider:
        async def eth_call(self, _address: str, selector: str) -> str:
            if selector == _selector("decimals()"):
                return "0x" + f"{18:064x}"
            return "0x"

        async def get_transaction(self, tx_hash: str) -> ChainTransaction:
            return ChainTransaction(tx_hash, 101, user_address, "0x" + "d" * 40)

    raw = RawChainEvent(
        chain_id=56,
        block_number=101,
        block_hash="0x" + "e" * 64,
        transaction_hash="0xmetadata-boundary",
        log_index=7,
        contract_address=pair_address,
        topics=[
            config.swap_topic,
            "0x" + ("0" * 24) + user_address[2:],
            "0x" + ("0" * 24) + user_address[2:],
        ],
        data="0x" + f"{2 * 10**18:064x}" + f"{0:064x}" + f"{0:064x}" + f"{10 * 10**18:064x}",
        timestamp=datetime(2026, 9, 15, tzinfo=UTC),
        provider="bsc",
        confirmation_status="confirmed",
    )

    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        session.add_all([
            Token(chain="bsc", address=quote_address, decimals=None),
            Token(chain="bsc", address=token_address, decimals=18),
            Pair(
                chain_id=56,
                address=pair_address,
                token0_address=quote_address,
                token1_address=token_address,
                quote_token=quote_address,
                factory_address="0x" + "f" * 40,
                creation_tx_hash="0xcreate",
                creation_block=100,
                creation_timestamp=datetime(2026, 9, 15, tzinfo=UTC),
            ),
            raw,
        ])
        await session.flush()
        resolver = TransactionContextResolver(Provider())
        pipeline = BSCMarketPipeline(
            session,
            metadata_provider=Provider(),
            transaction_resolver=resolver,
        )
        await pipeline.process_raw_event(raw)
        trade = await session.scalar(select(TradeEvent).where(TradeEvent.tx_hash == raw.transaction_hash))
        assert trade is not None
        assert trade.quote_amount == 2
        assert trade.native_amount == 2
        assert trade.price_quote == pytest.approx(Decimal("0.2"))
        await session.rollback()
    await engine.dispose()


@pytest.mark.asyncio
async def test_trade_snapshot_replay_is_idempotent() -> None:
    """Replaying one canonical trade must not violate snapshot identity."""
    from smms.market.snapshots import materialize_trade_snapshot

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        trade = TradeEvent(
            chain="bsc", chain_id=56, token_address="0x" + "1" * 40,
            pair_address="0x" + "2" * 40, trader_address="0x" + "3" * 40,
            tx_hash="0xsnapshot-replay", event_index=1, block_number=101,
            block_hash="0x" + "4" * 64, timestamp=datetime(2026, 9, 15, tzinfo=UTC),
            side=TradeSide.BUY, token_amount=Decimal(1), source="bsc:pancakeswap",
        )
        session.add(trade)
        await session.flush()
        await materialize_trade_snapshot(session, trade, as_of=trade.timestamp)
        await materialize_trade_snapshot(session, trade, as_of=trade.timestamp)
        assert await session.scalar(select(func.count()).select_from(TokenMarketSnapshot)) == 1
    await engine.dispose()

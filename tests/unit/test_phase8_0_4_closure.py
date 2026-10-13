from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from smms.chains.base import ChainLog, ChainTransaction
from smms.chains.bsc.active_pairs import ActivePairRegistry, DynamicPairSubscription
from smms.chains.bsc.transactions import TransactionContextResolver
from smms.db.models import (
    Base,
    Pair,
    RadarCandidate,
    RawChainEvent,
    Token,
    TokenMarketSnapshot,
    TradeEvent,
    TradeSide,
)
from smms.market.models import PairRecord
from smms.market.pipeline import BSCMarketPipeline
from smms.market.protocols import PancakeSwapAdapter, PancakeSwapConfig, ProtocolRegistry
from smms.market.snapshots import materialize_trade_snapshot
from smms.radar.config import RadarConfig
from smms.radar.engine import RadarEngine
from smms.runtime import materialize_dexscreener_enrichment


def _address(n: int) -> str:
    return "0x" + f"{n:040x}"


def _pair(address: str = "0x" + "1" * 40) -> PairRecord:
    return PairRecord(
        chain_id=56,
        address=address,
        token0=_address(2),
        token1=_address(3),
        factory=_address(4),
        dex="pancakeswap",
        quote_token=_address(3),
        creation_tx_hash="0xcreate",
        creation_block=100,
        creation_timestamp=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_pair_registry_is_bounded_and_expires_without_deleting_history():
    now = datetime(2026, 1, 1, tzinfo=UTC)
    registry = ActivePairRegistry(max_active_pairs=1, activity_ttl_seconds=10, clock=lambda: now)
    first = registry.add(_pair())
    second = registry.add(_pair("0x" + "5" * 40))
    assert first is not None and second is not None
    assert registry.count == 1
    assert registry.get(first.pair_address) is None
    registry.touch(second.pair_address, now + timedelta(seconds=11))
    registry.expire(now=now + timedelta(seconds=22))
    assert registry.count == 0


@pytest.mark.asyncio
async def test_pair_created_starts_dynamic_pair_subscription_path():
    class Stream:
        provider = "fixture"

        def __init__(self):
            self.settings = type("Settings", (), {"bsc_pair_subscription_batch_size": 2})()
            self.logs = []
            self.calls = []

        async def subscribe_logs(self, **kwargs):
            self.calls.append(kwargs)
            for item in self.logs:
                yield item

    factory = Stream()
    pair_stream = Stream()
    created = ChainLog(
        address=_address(4), topics=("pair",), data="0x", block_number=1,
        transaction_hash="0xcreate", log_index=0,
    )
    swap = ChainLog(
        address=_pair().address,
        topics=("swap",), data="0x", block_number=2,
        transaction_hash="0xswap", log_index=0,
    )
    factory.logs = [created]
    pair_stream.logs = [swap]
    registry = ActivePairRegistry(clock=lambda: datetime(2026, 1, 1, tzinfo=UTC))
    subscription = DynamicPairSubscription(
        factory,
        registry=registry,
        pair_stream_factory=lambda *_: pair_stream,
        pair_created_decoder=lambda _: _pair(),
        pair_topics=("swap",),
        refresh_interval_seconds=0.01,
    )
    iterator = subscription.subscribe_logs(address=None, topics=("pair",)).__aiter__()
    await iterator.__anext__()
    # The factory record is registered before the pair stream is consumed.
    assert registry.get(_pair().address) is not None
    assert (await iterator.__anext__()).address == _pair().address
    assert factory.calls == [{"address": None, "topics": ("pair",)}]
    assert pair_stream.calls == [{"address": (_pair().address.lower(),), "topics": ("swap",)}]
    await subscription.close()


@pytest.mark.asyncio
async def test_router_swap_uses_transaction_from_and_not_router_wallet():
    router = _address(10)
    user = _address(11)

    class Provider:
        async def get_transaction(self, tx_hash):
            return ChainTransaction(tx_hash, 1, user, router, input_data="0xswap")

    context = await TransactionContextResolver(Provider(), ttl_seconds=300).resolve(
        "0xTX", swap_sender=router, swap_recipient=_address(12)
    )
    assert context.transaction_from == user
    assert context.resolution == "ROUTER_TX_FROM"
    assert context.transaction_from != router


@pytest.mark.asyncio
async def test_native_amount_never_falls_back_to_usd_volume():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with db.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(db, expire_on_commit=False)
    async with factory() as session:
        trade = TradeEvent(
            chain="bsc", chain_id=56, token_address=_address(20), pair_address=_address(21),
            trader_address=_address(22), tx_hash="0xtx", event_index=0, block_number=1,
            block_hash="0xblock", timestamp=datetime.now(UTC), side=TradeSide.BUY,
            token_amount=Decimal(2), native_amount=Decimal("1.5"), value_usd=None,
            source="fixture", canonical=True, removed=False,
        )
        session.add(trade)
        await session.flush()
        snapshot = await materialize_trade_snapshot(session, trade)
        assert snapshot.volume_5m is None
    await db.dispose()


def test_dexscreener_enrichment_updates_only_discovered_pair_radar():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    token_address = _address(30)
    pair_address = _address(31)
    with Session(engine) as session:
        session.add(Token(chain="bsc", address=token_address, first_seen_at=datetime(2026, 1, 1, tzinfo=UTC)))
        pair = Pair(
            chain_id=56,
            address=pair_address,
            token0_address=token_address,
            token1_address=_address(32),
            quote_token=_address(32),
            creation_tx_hash="0xpair",
            creation_block=100,
            creation_timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        )
        session.add(pair)
        session.flush()
        RadarEngine(session, RadarConfig()).ingest(pair, at=datetime(2026, 1, 1, tzinfo=UTC))
        settings = type("Settings", (), {"bsc_chain_id": 56, "radar": RadarConfig()})()
        count = materialize_dexscreener_enrichment(
            session,
            [
                {
                    "token_address": token_address,
                    "pair_address": pair_address,
                    "dex_id": "pancakeswap",
                    "price_usd": "0.01",
                    "liquidity_usd": "100000",
                    "market_cap_usd": "1000000",
                    "volume_m5": "25000",
                    "txns_m5": {"buys": 4, "sells": 2},
                },
                {"token_address": _address(33), "pair_address": _address(34), "liquidity_usd": "999999"},
            ],
            settings=settings,
        )
        session.commit()
        assert count == 1
        assert session.query(TokenMarketSnapshot).count() == 1
        candidate = session.query(RadarCandidate).one()
        assert candidate.features_json["liquidity"] == "100000"
        assert candidate.features_json["market_cap"] == "1000000"
        assert candidate.features_json["volume_5m"] == "25000"
    engine.dispose()


def test_existing_market_snapshot_reports_no_activity_as_zero_not_unknown():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    at = datetime(2026, 1, 1, tzinfo=UTC)
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        token = Token(chain="bsc", address=_address(60), first_seen_at=at)
        session.add(token)
        session.flush()
        pair = Pair(chain_id=56, address=_address(61), token0_address=token.address,
                    token1_address=_address(62), quote_token=_address(62),
                    creation_tx_hash="0xcreate", creation_block=1, creation_timestamp=at)
        session.add(pair)
        session.flush()
        RadarEngine(session).ingest(pair, at=at)
        session.add(TokenMarketSnapshot(chain="bsc", chain_id=56, token_address=token.address,
                    pair_address=pair.address, observed_at=at, as_of=at, source="dexscreener",
                    status="OK", volume_1m=0, volume_5m=0, buys_1m=0, sells_1m=0,
                    buys_5m=0, sells_5m=0, unique_buyers_1m=0, unique_buyers_5m=0))
        session.flush()
        candidate = session.query(RadarCandidate).one()
        RadarEngine(session).refresh(candidate, at=at)
        assert candidate.features_json["buy_count_1m"] == 0
        assert candidate.features_json["sell_count_5m"] == 0
        assert candidate.features_json["unique_buyers_5m"] == 0
        assert candidate.features_json["reason_codes"].get("buy_count_1m") is None
    engine.dispose()


@pytest.mark.asyncio
async def test_metadata_backfill_is_retryable_and_records_failure_reason():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from smms.market.metadata import backfill_missing_erc20_metadata
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    class Provider:
        calls = 0
        async def eth_call(self, address, data):
            self.calls += 1
            raise RuntimeError("execution reverted")
    provider = Provider()
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        token = Token(chain="bsc", address=_address(70), first_seen_at=datetime.now(UTC))
        session.add(token)
        await session.commit()
        result = await backfill_missing_erc20_metadata(session, provider, batch_size=1, rate_limit_per_second=0, retries=0)
        await session.commit()
        assert result == {"attempted": 1, "updated": 0, "failed": 1}
        assert provider.calls == 3
        assert token.metadata_json["metadata_backfill_reason_code"] == "METADATA_PROVIDER_TIMEOUT"
        assert token.metadata_json["metadata_backfill_attempts"] == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_pipeline_uses_transaction_from_for_router_swap():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    pair_address = _address(40)
    token_address = _address(41)
    quote_address = _address(42)
    router_address = _address(43)
    user_address = _address(44)
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with db.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    class Provider:
        async def get_transaction(self, tx_hash):
            return ChainTransaction(tx_hash, 101, user_address, router_address, input_data="0xswap")

    async with async_sessionmaker(db, expire_on_commit=False)() as session:
        session.add_all(
            [
                Token(chain="bsc", address=token_address, decimals=18),
                Token(chain="bsc", address=quote_address, decimals=18),
                Pair(
                    chain_id=56,
                    address=pair_address,
                    token0_address=token_address,
                    token1_address=quote_address,
                    quote_token=quote_address,
                    creation_tx_hash="0xcreate",
                    creation_block=100,
                    creation_timestamp=datetime(2026, 1, 1, tzinfo=UTC),
                ),
            ]
        )
        raw = RawChainEvent(
            chain_id=56,
            block_number=101,
            block_hash="0xblock",
            transaction_hash="0xswap",
            log_index=0,
            contract_address=pair_address,
            topics=[
                PancakeSwapConfig().swap_topic,
                "0x" + f"{int(router_address, 16):064x}",
                "0x" + f"{int(user_address, 16):064x}",
            ],
            data="0x" + f"{0:064x}" + f"{100 * 10**18:064x}" + f"{2 * 10**18:064x}" + f"{0:064x}",
            timestamp=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
            provider="fixture",
            confirmation_status="confirmed",
        )
        session.add(raw)
        await session.flush()
        pipeline = BSCMarketPipeline(
            session,
            registry=ProtocolRegistry((PancakeSwapAdapter(PancakeSwapConfig()),)),
            transaction_resolver=TransactionContextResolver(Provider()),
        )
        await pipeline.process_raw_event(raw)
        trade = await session.scalar(
            select(TradeEvent).where(TradeEvent.tx_hash == "0xswap")
        )
        assert trade is not None
        assert trade.trader_address == user_address
        assert trade.trader_address != router_address
        assert trade.trader_resolution == "ROUTER_TX_FROM"
    await db.dispose()

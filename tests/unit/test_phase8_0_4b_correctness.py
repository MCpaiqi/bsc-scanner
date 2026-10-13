from __future__ import annotations

import asyncio
import importlib.util
import json
import shutil
import tempfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from smms.chains.base import ChainLog
from smms.chains.bsc.active_pairs import ActivePairRegistry, DynamicPairSubscription
from smms.chains.bsc.websocket import BSCWebsocketSubscription
from smms.chains.constants import BSC_CHAIN_ID
from smms.config import Settings
from smms.db.models import Base, MarketEvent, Pair, Token, TradeEvent, TradeSide
from smms.db.repositories.stream import AsyncStreamCheckpointRepository
from smms.ingestion.bsc_stream import BSCRealtimeIngestionService
from smms.ingestion.market.dexscreener import DexScreenerMarketProvider
from smms.ingestion.provider import DataUnavailable
from smms.main import _finalize_live_bsc_readiness
from smms.market.models import MarketEventRecord, PairRecord
from smms.market.models import TradeSide as MarketTradeSide
from smms.market.pipeline import BSCMarketPipeline
from smms.market.protocols import PancakeSwapConfig


def _address(value: int) -> str:
    return f"0x{value:040x}"


def _record(address: str, *, block: int = 100, created_at: datetime | None = None) -> PairRecord:
    return PairRecord(
        chain_id=BSC_CHAIN_ID,
        address=address,
        token0=_address(2),
        token1=_address(3),
        factory=_address(4),
        dex="pancakeswap",
        quote_token=_address(3),
        creation_tx_hash="0xcreate",
        creation_block=block,
        creation_timestamp=created_at,
    )


def _trade(pair: str, stamp: datetime, index: int) -> TradeEvent:
    return TradeEvent(
        chain="bsc",
        chain_id=BSC_CHAIN_ID,
        token_address=_address(2),
        pair_address=pair,
        trader_address=_address(10 + index),
        tx_hash=f"0xtrade{index}",
        event_index=index,
        block_number=100 + index,
        block_hash=f"0xblock{index}",
        timestamp=stamp,
        side=TradeSide.BUY,
        source="fixture",
        canonical=True,
        removed=False,
    )


@pytest.mark.asyncio
async def test_bootstrap_restores_newest_top_n_for_activity_10_9_8_7() -> None:
    now = datetime(2026, 1, 1, 1, 0, tzinfo=UTC)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    pairs = [
        Pair(
            chain_id=BSC_CHAIN_ID,
            address=_address(index),
            token0_address=_address(2),
            token1_address=_address(3),
            quote_token=_address(3),
            factory_address=_address(4),
            creation_block=index,
            creation_timestamp=now - timedelta(minutes=5),
            creation_tx_hash=f"0xcreate{index}",
        )
        for index in range(1, 5)
    ]
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        session.add_all(pairs)
        for index, (pair, priority) in enumerate(zip(pairs, (10, 9, 8, 7), strict=True)):
            session.add(_trade(pair.address, now - timedelta(seconds=20 - priority), index))
        await session.commit()
        registry = ActivePairRegistry(max_active_pairs=2, activity_ttl_seconds=3600, clock=lambda: now)
        result = await registry.bootstrap(session, now=now)
        assert set(registry.addresses()) == {_address(1), _address(2)}
        assert result["active_pairs_current"] == 2
    await engine.dispose()


@pytest.mark.asyncio
async def test_bootstrap_ignores_expired_pair() -> None:
    now = datetime(2026, 1, 1, 1, 0, tzinfo=UTC)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        session.add(Pair(
            chain_id=56, address=_address(99), token0_address=_address(2),
            token1_address=_address(3), quote_token=_address(3),
            creation_tx_hash="0xexpired", creation_block=1,
            creation_timestamp=now - timedelta(hours=2),
        ))
        await session.commit()
        registry = ActivePairRegistry(activity_ttl_seconds=3600, clock=lambda: now)
        await registry.bootstrap(session, now=now)
        assert registry.addresses() == ()
    await engine.dispose()


@pytest.mark.asyncio
async def test_epoch_bootstrap_filters_lanes_before_activity_limit() -> None:
    now = datetime(2026, 1, 1, 1, 0, tzinfo=UTC)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    epoch = "bsc_live_test"
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        repo = AsyncStreamCheckpointRepository(session)
        for index in range(1, 8):
            session.add(Pair(
                chain_id=56, address=_address(index), token0_address=_address(20),
                token1_address=_address(30), quote_token=_address(30),
                factory_address=_address(40), creation_tx_hash=f"0xcreate{index}",
                creation_block=index, canonical=index != 4, removed=index == 5,
                creation_timestamp=now - timedelta(seconds=120 if index == 1 else 30),
            ))
            if index == 7:
                continue  # A canonical recent pair without a lane is not owned.
            await repo.upsert(
                chain_id=56 if index != 6 else 1,
                provider="bsc" if index != 3 else "other",
                stream_name=f"{epoch if index != 2 else 'other_epoch'}:pair:{_address(index)}",
                status="running", block_number=100,
            )
        await session.commit()
        registry = ActivePairRegistry(max_active_pairs=1, clock=lambda: now)
        await registry.bootstrap(session, now=now, stream_name=epoch)
        assert registry.addresses() == (_address(1),)
        empty = ActivePairRegistry(clock=lambda: now)
        await empty.bootstrap(session, now=now, stream_name="new_epoch")
        assert empty.addresses() == ()
    await engine.dispose()


def test_pair_created_replay_does_not_regress_activity() -> None:
    creation = datetime(2026, 1, 1, tzinfo=UTC)
    touch = creation + timedelta(minutes=20)
    registry = ActivePairRegistry(clock=lambda: touch)
    address = _address(101)
    item = registry.add(_record(address, created_at=creation))
    assert item is not None
    registry.touch(address, touch)
    registry.add(_record(address, created_at=creation))
    assert registry.get(address).last_activity_at == touch
    assert registry.get(address).created_at == creation


@pytest.mark.asyncio
async def test_new_pair_activation_replay_catches_same_block_swap_without_duplicate() -> None:
    pair_address = _address(201)
    created_log = ChainLog(
        address=_address(4),
        topics=("PairCreated",),
        data="0x",
        block_number=100,
        transaction_hash="0xcreate",
        log_index=10,
    )
    swap_log = ChainLog(
        address=pair_address,
        topics=("Swap",),
        data="0x",
        block_number=100,
        transaction_hash="0xswap",
        log_index=20,
        block_hash="0xfork-a",
    )
    later_log = ChainLog(
        address=pair_address,
        topics=("Swap",),
        data="0x",
        block_number=101,
        transaction_hash="0xlater",
        log_index=1,
        block_hash="0xfork-a-101",
    )

    class Factory:
        provider = "fixture"
        settings = type("Settings", (), {"bsc_pair_subscription_batch_size": 50})()

        async def subscribe_logs(self, **_kwargs):
            yield created_log

    class PairStream:
        async def subscribe_logs(self, **_kwargs):
            # WS starts late, buffers a newer log, then repeats the replayed swap.
            yield later_log
            yield swap_log

        async def close(self):
            return None

    class Provider:
        async def get_latest_block_number(self):
            return 100

        async def get_logs(self, **_kwargs):
            return [swap_log]

    registry = ActivePairRegistry(clock=lambda: datetime(2026, 1, 1, tzinfo=UTC))
    subscription = DynamicPairSubscription(
        Factory(),
        registry=registry,
        pair_stream_factory=lambda *_: PairStream(),
        pair_created_decoder=lambda _log: _record(pair_address, block=100),
        pair_topics=("Swap",),
        activation_replay_provider=Provider(),
        refresh_interval_seconds=0.01,
    )
    iterator = subscription.subscribe_logs(). __aiter__()
    received = [
        await iterator.__anext__(),
        await iterator.__anext__(),
        await iterator.__anext__(),
    ]
    assert [item.transaction_hash for item in received] == ["0xcreate", "0xswap", "0xlater"]
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(iterator.__anext__(), timeout=0.05)
    await subscription.close()


@pytest.mark.asyncio
async def test_activation_batch_catchup_stays_buffered_until_pair_handoff() -> None:
    pair_address = _address(202)
    created_log = ChainLog(
        address=_address(5), topics=("PairCreated",), data="0x", block_number=100,
        transaction_hash="0xcreate-batch", log_index=10,
    )
    out_of_order_log = ChainLog(
        address=pair_address, topics=("Swap",), data="0x", block_number=101,
        transaction_hash="0xswap-101", log_index=1, block_hash="0xfork-101",
    )
    same_block_log = ChainLog(
        address=pair_address, topics=("Swap",), data="0x", block_number=100,
        transaction_hash="0xswap-100", log_index=20, block_hash="0xfork-100",
    )

    class Factory:
        provider = "fixture"
        settings = type("Settings", (), {"bsc_pair_subscription_batch_size": 50})()

        async def subscribe_logs(self, **_kwargs):
            yield created_log

    class PairStream:
        async def subscribe_logs(self, **_kwargs):
            yield out_of_order_log

        async def close(self):
            return None

    class Provider:
        async def get_latest_block_number(self):
            return 101

        async def get_logs(self, **_kwargs):
            return [same_block_log, out_of_order_log]

    registry = ActivePairRegistry(clock=lambda: datetime(2026, 1, 1, tzinfo=UTC))
    subscription = DynamicPairSubscription(
        Factory(), registry=registry, pair_stream_factory=lambda *_: PairStream(),
        pair_created_decoder=lambda _log: _record(pair_address, block=100),
        pair_topics=("Swap",), activation_replay_provider=Provider(),
        refresh_interval_seconds=0.01,
    )
    iterator = subscription.subscribe_logs().__aiter__()
    received = [await iterator.__anext__() for _ in range(3)]
    assert [item.transaction_hash for item in received] == [
        "0xcreate-batch", "0xswap-100", "0xswap-101"
    ]
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(iterator.__anext__(), timeout=0.05)
    await subscription.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("global_block, expected_start", [(1000, 938), (900, 888)])
async def test_slowest_subscription_lane_is_used_for_recovery(global_block, expected_start) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        repo = AsyncStreamCheckpointRepository(session)
        pair_a, pair_b = _address(551), _address(552)
        await repo.upsert(chain_id=56, provider="bsc", stream_name="bsc_realtime", status="running", block_number=global_block)
        await repo.upsert(chain_id=56, provider="bsc", stream_name=f"bsc_realtime:pair:{pair_a}", status="running", block_number=1000)
        await repo.upsert(chain_id=56, provider="bsc", stream_name=f"bsc_realtime:pair:{pair_b}", status="running", block_number=950)
        await session.commit()
        safe = await repo.get_safest(56, "bsc", "bsc_realtime:pair:")
        assert safe is not None and safe.last_processed_block == 950
        registry = ActivePairRegistry(subscription_batch_size=1)
        registry.add(_record(pair_a, created_at=datetime.now(UTC)))
        registry.add(_record(pair_b, created_at=datetime.now(UTC)))
        service = BSCRealtimeIngestionService(
            type("Stream", (), {})(), session=session, active_pair_registry=registry
        )
        global_checkpoint = await repo.get(56, "bsc", "bsc_realtime")
        assert await service.recovery_from_block(global_checkpoint, lookback=12) == expected_start
    await engine.dispose()


@pytest.mark.asyncio
async def test_recovery_scanner_discovers_pair_before_same_block_pair_query() -> None:
    factory, pair_address = _address(4), _address(553)
    config = PancakeSwapConfig(factory_addresses=(factory,), quote_assets=(_address(3),))

    def word(value: int) -> str:
        return f"{value:064x}"

    created = ChainLog(
        address=factory,
        topics=(config.pair_created_topic, "0x" + word(2), "0x" + word(3)),
        data="0x" + word(int(pair_address, 16)) + word(1),
        block_number=100,
        transaction_hash="0xrecover-create",
        log_index=10,
    )
    swap = ChainLog(
        address=pair_address,
        topics=(config.swap_topic,),
        data="0x",
        block_number=100,
        transaction_hash="0xrecover-swap",
        log_index=20,
        block_hash="0xrecover-block",
    )
    calls: list[dict[str, object]] = []

    class Provider:
        async def get_latest_block_number(self):
            return 100

        async def get_logs(self, **kwargs):
            calls.append(kwargs)
            if not kwargs["from_block"] <= 100 <= kwargs["to_block"]:
                return []
            return [created] if kwargs["topics"] == (config.pair_created_topic,) else [swap]

    class FiniteStream:
        provider = "fixture"
        settings = type("Settings", (), {
            "bsc_dex_factory_addresses": factory,
            "bsc_quote_tokens": _address(3),
            "bsc_reorg_lookback_blocks": 12,
            "bsc_log_chunk_size": 10,
        })()

        async def subscribe_logs(self, **_kwargs):
            if False:
                yield None

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        stream = FiniteStream()
        registry = ActivePairRegistry()
        service = BSCRealtimeIngestionService(
            stream, session=session, chain_provider=Provider(), active_pair_registry=registry
        )
        service._build_dynamic_stream = lambda *_args: stream
        processed: list[ChainLog] = []

        async def process(log: ChainLog):
            processed.append(log)
            if log is created:
                registry.add(_record(pair_address, block=100, created_at=datetime.now(UTC)))

        async def confirm(_block: int):
            return 0

        service.process_log = process
        service.confirm_through = confirm
        await service.run()
        checkpoint = await AsyncStreamCheckpointRepository(session).get(
            56, "bsc", f"bsc_realtime:pair:{pair_address}"
        )
        assert [item.transaction_hash for item in processed] == [
            "0xrecover-create", "0xrecover-swap"
        ]
        pair_calls = [call for call in calls if call["topics"] != (config.pair_created_topic,)]
        assert pair_address in pair_calls[0]["address"]
        assert checkpoint is not None and checkpoint.last_processed_block == 100
    await engine.dispose()


def _dex_pair(address: str, *, chain: str = "bsc") -> dict[str, object]:
    return {
        "chainId": chain,
        "pairAddress": address,
        "baseToken": {"address": _address(300), "name": "Token", "symbol": "TOK"},
        "quoteToken": {"address": _address(301), "symbol": "WBNB"},
        "dexId": "pancakeswap",
        "priceUsd": "1",
    }


@pytest.mark.asyncio
async def test_dexscreener_75_pair_chunking_uses_30_30_15_requests() -> None:
    addresses = [_address(400 + index) for index in range(75)]
    calls: list[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        chunk = request.url.path.rsplit("/", 1)[-1].split(",")
        calls.append(chunk)
        pairs = [_dex_pair(address) for address in chunk]
        pairs.extend([_dex_pair(_address(999)), _dex_pair(_address(998), chain="ethereum")])
        return httpx.Response(200, json={"pairs": pairs})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://dex") as client:
        provider = DexScreenerMarketProvider(client=client, rate_limit_per_second=0, targeted_batch_size=30)
        payload = await provider.fetch_pairs(addresses)
    assert [len(chunk) for chunk in calls] == [30, 30, 15]
    assert {item["pair_address"] for item in payload["tokens"]} == {address for address in addresses}


@pytest.mark.asyncio
async def test_dexscreener_targeted_lookup_returns_requested_pair() -> None:
    address = _address(501)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"pairs": [_dex_pair(address)]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://dex") as client:
        payload = await DexScreenerMarketProvider(client=client, rate_limit_per_second=0).fetch_pairs([address])
    assert [item["pair_address"] for item in payload["tokens"]] == [address]


@pytest.mark.asyncio
async def test_dexscreener_rejects_unexpected_pair_and_wrong_chain() -> None:
    requested = _address(510)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"pairs": [
            _dex_pair(_address(511)),
            _dex_pair(requested, chain="ethereum"),
        ]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://dex") as client:
        payload = await DexScreenerMarketProvider(client=client, rate_limit_per_second=0).fetch_pairs([requested])
    assert payload["tokens"] == []


@pytest.mark.asyncio
async def test_dexscreener_null_pairs_is_a_bounded_diagnostic(caplog) -> None:
    """A malformed provider body is non-canonical but remains actionable."""
    addresses = [_address(600 + index) for index in range(35)]

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"pairs": None})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://dex") as client:
        provider = DexScreenerMarketProvider(client=client, rate_limit_per_second=0)
        with pytest.raises(DataUnavailable, match="invalid pair response"):
            await provider.fetch_pairs(addresses)
    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "status=200" in messages
    assert "requested_pair_count=30" in messages
    assert "pair_address_sample=" in messages
    assert addresses[-1] not in messages


@pytest.mark.asyncio
async def test_ws_probe_requires_chain_subscription_and_unsubscribe() -> None:
    class Socket:
        def __init__(self):
            self.sent: list[dict[str, object]] = []

        async def send(self, value: str):
            self.sent.append(json.loads(value))

        async def recv(self) -> str:
            request = self.sent[-1]
            if request["method"] == "eth_chainId":
                return json.dumps({"result": "0x38"})
            if request["method"] == "eth_subscribe":
                return json.dumps({"result": "0xsub"})
            return json.dumps({"result": True})

        async def close(self):
            return None

    socket = Socket()
    settings = Settings(
        _env_file=None,
        BSC_WS_URL="wss://fixture",
        BSC_DEX_FACTORY_ADDRESSES=_address(4),
    )
    stream = BSCWebsocketSubscription(settings, connect=lambda *_args, **_kwargs: socket)
    result = await stream.probe_smoke()
    assert result["ready"] is True
    assert result["ws_chain_id"] == 56
    assert [item["method"] for item in socket.sent] == ["eth_chainId", "eth_subscribe", "eth_unsubscribe"]
    subscription_filter = socket.sent[1]["params"][1]
    assert subscription_filter["address"] == _address(4)


@pytest.mark.asyncio
async def test_removed_log_without_block_hash_preserves_both_forks() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        session.add_all([
            TradeEvent(
                chain="bsc", chain_id=56, token_address=_address(1), pair_address=_address(2),
                trader_address=_address(3), tx_hash="0xsame", event_index=7, block_number=100,
                block_hash="0xfork-a", timestamp=datetime.now(UTC), side=TradeSide.BUY,
                source="fixture", canonical=True, removed=False,
            ),
            TradeEvent(
                chain="bsc", chain_id=56, token_address=_address(1), pair_address=_address(2),
                trader_address=_address(3), tx_hash="0xsame", event_index=7, block_number=100,
                block_hash="0xfork-b", timestamp=datetime.now(UTC), side=TradeSide.BUY,
                source="fixture", canonical=True, removed=False,
            ),
        ])
        await session.flush()
        raw = type("Removed", (), {
            "transaction_hash": "0xsame", "log_index": 7, "block_hash": None,
            "raw_payload_json": {},
        })()
        await BSCMarketPipeline(session)._invalidate_event(raw)
        rows = list((await session.scalars(select(TradeEvent))).all())
        assert all(row.canonical and not row.removed for row in rows)
        assert raw.raw_payload_json["reorg_evidence"] == "UNRESOLVED_MISSING_BLOCK_HASH"
    await engine.dispose()


@pytest.mark.asyncio
async def test_fork_specific_invalidation_only_removes_matching_block_hash() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        for block_hash in ("0xfork-a", "0xfork-b"):
            session.add(TradeEvent(
                chain="bsc", chain_id=56, token_address=_address(1), pair_address=_address(2),
                trader_address=_address(3), tx_hash="0xsame", event_index=7, block_number=100,
                block_hash=block_hash, timestamp=datetime.now(UTC), side=TradeSide.BUY,
                source="fixture", canonical=True, removed=False,
            ))
            session.add(MarketEvent(
                chain_id=56, event_type="TRADE", tx_hash="0xsame", event_index=7,
                block_number=100, block_hash=block_hash, timestamp=datetime.now(UTC),
                source="fixture", metadata_json={},
            ))
        await session.flush()
        raw = type("Removed", (), {
            "transaction_hash": "0xsame", "log_index": 7, "block_hash": "0xfork-a",
            "raw_payload_json": {},
        })()
        await BSCMarketPipeline(session)._invalidate_event(raw)
        trades = {row.block_hash: row for row in (await session.scalars(select(TradeEvent))).all()}
        events = {row.block_hash: row for row in (await session.scalars(select(MarketEvent))).all()}
        assert trades["0xfork-a"].removed and not trades["0xfork-a"].canonical
        assert trades["0xfork-b"].canonical and not trades["0xfork-b"].removed
        assert events["0xfork-a"].metadata_json["orphaned"] is True
        assert "orphaned" not in events["0xfork-b"].metadata_json
    await engine.dispose()


@pytest.mark.asyncio
async def test_stable_bucket_only_rebuilds_affected_bucket() -> None:
    registry = ActivePairRegistry(subscription_batch_size=2)
    first, second, third = _address(601), _address(602), _address(603)
    for address in (first, second, third):
        registry.add(_record(address, created_at=datetime.now(UTC)))

    class BlockingStream:
        async def subscribe_logs(self, **_kwargs):
            await asyncio.Event().wait()
            if False:
                yield None

        async def close(self):
            return None

    subscription = DynamicPairSubscription(
        BlockingStream(), registry=registry, pair_stream_factory=lambda *_: BlockingStream(),
        pair_created_decoder=lambda _log: None, pair_topics=("Swap",),
    )
    await subscription._ensure_pair_tasks()
    original = dict(subscription._tasks)
    registry.remove(first)
    registry.add(_record(_address(604), created_at=datetime.now(UTC)))
    await subscription._ensure_pair_tasks()
    unaffected_key = next(key for key in original if third in key)
    assert subscription._tasks[unaffected_key] is original[unaffected_key]
    await subscription.close()


def test_checkpoint_propagation_updates_existing_pair_streams_100_to_500() -> None:
    class Observed:
        provider = "fixture"

        def __init__(self):
            self.values: list[int] = []

        def observe_checkpoint(self, block, *_args):
            self.values.append(block)

    factory = Observed()
    pair_stream = Observed()
    subscription = DynamicPairSubscription(
        factory, registry=ActivePairRegistry(), pair_stream_factory=lambda *_: pair_stream,
        pair_created_decoder=lambda _log: None, pair_topics=("Swap",),
    )
    subscription._streams[("pair",)] = pair_stream
    subscription.observe_checkpoint(100)
    subscription.observe_checkpoint(500)
    assert pair_stream.values == []
    subscription.observe_pair_checkpoint("pair", 100)
    subscription.observe_pair_checkpoint("pair", 500)
    assert factory.values == [100, 500]
    assert pair_stream.values == [100, 500]


async def _persist_quote_trade(quote_address: str) -> TradeEvent:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session = async_sessionmaker(engine, expire_on_commit=False)()
    token_address, pair_address = _address(701), _address(702)
    session.add_all([
        Token(chain="bsc", address=token_address, decimals=18),
        Token(chain="bsc", address=quote_address, decimals=18),
    ])
    pair = Pair(
        chain_id=56, address=pair_address, token0_address=token_address,
        token1_address=quote_address, quote_token=quote_address,
        creation_tx_hash="0xcreate", creation_block=1,
        creation_timestamp=datetime.now(UTC),
    )
    session.add(pair)
    await session.flush()
    record = MarketEventRecord(
        event_type="TRADE", chain_id=56, token=token_address, pair=pair_address,
        wallet=_address(703), tx_hash=f"0x{quote_address[-8:]}", block=2,
        timestamp=datetime.now(UTC), side=MarketTradeSide.BUY,
        token_amount=Decimal(10**18), quote_amount=Decimal(2 * 10**18),
        source="fixture", metadata={"log_index": 0, "block_hash": "0xblock"},
    )
    trade = await BSCMarketPipeline(session)._persist_trade(record, pair)
    assert trade is not None
    await session.commit()
    await session.close()
    await engine.dispose()
    return trade


@pytest.mark.asyncio
async def test_usdt_quote_native_amount_is_null() -> None:
    trade = await _persist_quote_trade(_address(704))
    assert trade.quote_amount == Decimal(2)
    assert trade.native_amount is None


@pytest.mark.asyncio
async def test_wbnb_quote_native_amount_has_value() -> None:
    trade = await _persist_quote_trade("0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c")
    assert trade.native_amount == Decimal(2)


def _ready_payload() -> dict[str, object]:
    return {
        "chain_id": 56,
        "database": "OK",
        "migration": {"current": "20260913_0027", "head": "20260913_0027", "ready": True},
        "rpc": "OK",
        "factory": [{"address": _address(4), "deployed": True}],
        "redis": "OK",
    }


def test_factory_missing_is_degraded_and_not_ingestion_ready() -> None:
    payload = _ready_payload()
    payload["factory"] = "DEGRADED:FACTORY_NOT_CONFIGURED"
    _finalize_live_bsc_readiness(payload, ["FACTORY_NOT_CONFIGURED"])
    assert payload["ingestion_ready"] is False
    assert "FACTORY_NOT_CONFIGURED" in payload["reason"]


def test_redis_unavailable_disables_fanout_not_ingestion() -> None:
    payload = _ready_payload()
    payload["redis"] = "DEGRADED"
    _finalize_live_bsc_readiness(payload, [])
    assert payload["fanout_ready"] is False
    assert payload["ingestion_ready"] is True


def test_database_unavailable_disables_ingestion_readiness() -> None:
    payload = _ready_payload()
    payload["database"] = "DEGRADED"
    _finalize_live_bsc_readiness(payload, ["DATABASE_UNAVAILABLE"])
    assert payload["ingestion_ready"] is False


def test_wrong_chain_disables_ingestion_readiness() -> None:
    payload = _ready_payload()
    payload["chain_id"] = 1
    _finalize_live_bsc_readiness(payload, ["WRONG_CHAIN"])
    assert payload["ingestion_ready"] is False


def test_migration_behind_head_disables_ingestion_readiness() -> None:
    payload = _ready_payload()
    payload["migration"] = {"current": "20260913_0026", "head": "20260913_0027", "ready": False}
    _finalize_live_bsc_readiness(payload, ["DATABASE_MIGRATION_NOT_HEAD"])
    assert payload["ingestion_ready"] is False
    assert "DATABASE_MIGRATION_NOT_HEAD" in payload["reason"]


def test_migration_diagnostics_remains_structured_data() -> None:
    payload = _ready_payload()
    _finalize_live_bsc_readiness(payload, [])
    assert payload["migration"] == {
        "current": "20260913_0027",
        "head": "20260913_0027",
        "ready": True,
    }


def test_release_artifact_excludes_sensitive_and_recursive_paths() -> None:
    script = Path(__file__).parents[2] / "scripts" / "build_release.py"
    spec = importlib.util.spec_from_file_location("phase804b_build_release", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    temp_root = Path(tempfile.mkdtemp(prefix="phase804b-release-", dir=Path.cwd()))
    try:
        root = temp_root / "project"
        (root / "scripts").mkdir(parents=True)
        (root / "scripts" / "release_audit.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
        for path in (root / ".git", root / "release", root / "release-debug", root / "node_modules", root / ".pytest_cache"):
            path.mkdir()
            (path / "secret.txt").write_text("secret", encoding="utf-8")
        (root / ".env").write_text("PRIVATE_KEY=secret\n", encoding="utf-8")
        (root / ".env.example").write_text("PRIVATE_KEY=\n", encoding="utf-8")
        archive = module.build_release(root, root / "artifact")
        output = archive.with_suffix("")
        assert (output / ".env.example").exists()
        assert not (output / ".env").exists()
        assert not any(part in {".git", "release", "release-debug", "node_modules", ".pytest_cache"} for path in output.rglob("*") for part in path.relative_to(output).parts)
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)

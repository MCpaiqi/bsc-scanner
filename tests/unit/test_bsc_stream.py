import asyncio
import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from smms.chains.base import ChainLog
from smms.chains.bsc.websocket import BSCWebsocketSubscription
from smms.config import Settings
from smms.db.models import (
    Base,
    MarketEvent,
    Pair,
    RawChainEvent,
    RuntimeWorkerStatus,
    StreamCheckpoint,
)
from smms.db.repositories import RawChainEventRepository, StreamCheckpointRepository
from smms.db.repositories.stream import AsyncStreamCheckpointRepository
from smms.ingestion.bsc_stream import BSCRealtimeIngestionService, pair_event_topic_filter
from smms.ingestion.provider import DataUnavailable
from smms.market.protocols import PancakeSwapConfig


class FakeWebsocket:
    def __init__(self, messages, *, fail_after=False):
        self.messages = list(messages)
        self.fail_after = fail_after
        self.sent = []
        self.closed = False

    async def send(self, value):
        self.sent.append(json.loads(value))

    async def recv(self):
        request = self.sent[-1]
        result = "0x38" if request["method"] == "eth_chainId" else "sub"
        return json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result})

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.messages:
            return self.messages.pop(0)
        if self.fail_after:
            raise ConnectionError("dropped")
        raise StopAsyncIteration

    async def close(self):
        self.closed = True


def _message(tx="0xTx", index="0x1", removed=False):
    return json.dumps({"method": "eth_subscription", "params": {"result": {
        "address": "0xContract", "topics": ["0xTopic"], "data": "0x00",
        "blockNumber": "0x10", "blockHash": "0xBlock", "transactionHash": tx,
        "transactionIndex": "0x0", "logIndex": index, "removed": removed,
    }}})


@pytest.mark.asyncio
async def test_websocket_reconnects_deduplicates_and_marks_removed():
    sockets = [FakeWebsocket([_message(), _message()]), FakeWebsocket([_message(removed=True)])]

    async def connect(*args, **kwargs):
        return sockets.pop(0)

    settings = Settings(_env_file=None, BSC_WS_URL="wss://bsc", BSC_WS_RECONNECT_MAX_RETRIES=1, BSC_WS_BACKOFF_SECONDS=0, BSC_WS_JITTER_SECONDS=0)
    stream = BSCWebsocketSubscription(settings, connect=connect)
    results = []
    async for log in stream.subscribe_logs():
        results.append(log)
        if len(results) == 2:
            await stream.close()
    assert results[0].identity == ("0xtx", 1)
    assert results[1].removed is True
    assert stream.metrics.reconnect_count == 1


def test_stream_schema_and_storage_are_idempotent():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    assert {"raw_chain_events", "stream_checkpoints"} <= set(inspect(engine).get_table_names())
    with Session(engine) as session:
        event = RawChainEvent(chain_id=56, block_number=1, block_hash="0xb", transaction_hash="0xt", log_index=2, contract_address="0xc", topics=["0x0"], data="0x", ingestion_time=datetime.now(UTC), provider="fixture", confirmation_status="pending", removed=False, raw_payload_json={})
        first = RawChainEventRepository(session).add_idempotent(event)
        second = RawChainEventRepository(session).add_idempotent(RawChainEvent(chain_id=56, block_number=1, block_hash="0xb", transaction_hash="0xt", log_index=2, contract_address="0xc", topics=["0x0"], data="0x", ingestion_time=datetime.now(UTC), provider="fixture", confirmation_status="confirmed", removed=False, raw_payload_json={"x": 1}))
        checkpoint = StreamCheckpointRepository(session).upsert(chain_id=56, provider="fixture", stream_name="bsc_logs", status="running", block_number=1, log_index=2, transaction_hash="0xt")
        same = StreamCheckpointRepository(session).upsert(chain_id=56, provider="fixture", stream_name="bsc_logs", status="running", block_number=0, log_index=1, transaction_hash="old")
        session.commit()
        assert first.id == second.id
        assert second.confirmation_status == "confirmed"
        assert checkpoint.id == same.id
        assert same.last_processed_block == 1
        assert session.query(RawChainEvent).count() == 1
        assert session.query(StreamCheckpoint).count() == 1


def test_websocket_rejects_malformed_logs():
    with pytest.raises(DataUnavailable):
        BSCWebsocketSubscription._normalize_log({"address": "0xC"})


def test_pair_event_topic_filter_uses_a_single_topic0_or_slot() -> None:
    """Swap/Sync/Mint/Burn are alternatives, not four indexed topic positions."""
    config = PancakeSwapConfig()
    assert pair_event_topic_filter(config) == ((
        config.swap_topic,
        config.sync_topic,
        config.mint_topic,
        config.burn_topic,
    ),)


def test_ingestion_phase_requires_stable_live_edge_not_just_a_running_worker() -> None:
    from smms.ingestion.bsc_stream import ingestion_phase

    assert ingestion_phase(
        head_block=1_000,
        checkpoint_block=990,
        stable_chunks=9,
        lag_threshold=3,
        required_stable_chunks=2,
    ) == ("CATCHING_UP", False)
    assert ingestion_phase(
        head_block=1_000,
        checkpoint_block=999,
        stable_chunks=1,
        lag_threshold=3,
        required_stable_chunks=2,
    ) == ("CATCHING_UP", False)
    assert ingestion_phase(
        head_block=1_000,
        checkpoint_block=999,
        stable_chunks=2,
        lag_threshold=3,
        required_stable_chunks=2,
    ) == ("LIVE", True)


def test_rpc_method_summary_keeps_transaction_and_timestamp_latency_separate() -> None:
    from smms.ingestion.bsc_stream import rpc_method_summary

    summary = rpc_method_summary([
        {"method": "eth_getLogs", "latency_ms": 10},
        {"method": "eth_getTransactionByHash", "latency_ms": 20},
        {"method": "eth_getTransactionByHash", "latency_ms": 30},
        {"method": "eth_getBlockByNumber", "latency_ms": 40},
    ])

    assert summary["counts"] == {
        "eth_getLogs": 1,
        "eth_getTransactionByHash": 2,
        "eth_getBlockByNumber": 1,
    }
    assert summary["latency_ms"] == {
        "eth_getLogs": 10.0,
        "eth_getTransactionByHash": 50.0,
        "eth_getBlockByNumber": 40.0,
    }


@pytest.mark.asyncio
async def test_successful_chunk_persists_live_edge_progress() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    class Stream:
        settings = Settings(_env_file=None, BSC_RPC_URL="https://fixture")

    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        session.add(
            RuntimeWorkerStatus(
                worker_name="bsc-ingestion",
                instance_id="failed-worker",
                status="DEGRADED",
                last_error="previous chunk failed",
            )
        )
        await session.flush()
        service = BSCRealtimeIngestionService(Stream(), session=session)
        await service.record_chunk_progress(
            head_block=1_000,
            checkpoint_block=990,
            chunk_started_at=datetime.now(UTC),
            chunk_duration_seconds=1.25,
            transport="http",
            endpoint_index=0,
            counters={"returned_log_count": 7, "swap_count": 3},
        )
        status = await session.scalar(
            select(RuntimeWorkerStatus).where(RuntimeWorkerStatus.worker_name == "bsc-ingestion")
        )
        assert status is not None
        assert status.status == "CATCHING_UP"
        assert status.head_block == 1_000
        assert status.checkpoint_block == 990
        assert status.block_lag == 10
        assert status.ingestion_phase == "CATCHING_UP"
        assert status.live_ready is False
        assert status.last_processed_at is not None
        assert status.last_error is None
    await engine.dispose()


@pytest.mark.asyncio
async def test_catchup_snapshot_flush_deduplicates_latest_trade_per_token(monkeypatch) -> None:
    """Catch-up materializes one aggregate snapshot per affected token."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    class Stream:
        settings = Settings(_env_file=None, BSC_RPC_URL="https://fixture")

    calls: list[str] = []

    async def snapshot(_session, trade, *, as_of=None):
        calls.append(trade.token_address)

    monkeypatch.setattr("smms.market.snapshots.materialize_trade_snapshot", snapshot)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        service = BSCRealtimeIngestionService(Stream(), session=session)
        service._deferred_trades = [
            type("Trade", (), {"token_address": "0xtarget", "timestamp": datetime.now(UTC)})(),
            type("Trade", (), {"token_address": "0xtarget", "timestamp": datetime.now(UTC)})(),
        ]
        await service.flush_deferred_snapshots()
    assert calls == ["0xtarget"]
    await engine.dispose()


@pytest.mark.asyncio
async def test_live_tail_uses_its_own_checkpoint_and_starts_at_head_lookback(monkeypatch) -> None:
    """A live tail does not inherit a distant historical backfill cursor."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    factory = "0x" + "1" * 40
    calls: list[dict] = []

    class Provider:
        async def get_latest_block_number(self):
            return 1_000

        async def get_logs(self, **kwargs):
            calls.append(kwargs)
            return []

    class Stream:
        settings = Settings(
            _env_file=None,
            BSC_RPC_URL="https://fixture",
            BSC_DEX_FACTORY_ADDRESSES=factory,
            BSC_REORG_LOOKBACK_BLOCKS=12,
        )

        async def prepare_factory_candidate(self, **_kwargs):
            return None

        async def subscribe_logs(self, **_kwargs):
            return
            yield

    async def no_op(_value):
        return None

    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        service = BSCRealtimeIngestionService(
            Stream(),
            session=session,
            chain_provider=Provider(),
            stream_name="bsc_realtime_live_tail",
            worker_name="bsc-ingestion",
            start_from_head=True,
        )
        service._build_dynamic_stream = lambda *_args: service.stream
        service.process_log = no_op
        service.confirm_through = no_op
        await service.run()

    assert calls[0]["from_block"] == 988
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("resume", [False, True])
async def test_live_tail_restores_only_its_epoch_pairs_before_recovery(resume) -> None:
    """Retries retain owned pair lanes; fresh epochs keep historical pools out."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory, token, quote = ("0x" + digit * 40 for digit in "123")
    owned = {"0x" + digit * 40 for digit in "45"}
    unrelated = "0x" + "6" * 40
    epoch = "bsc_realtime_live_tail_test"
    calls = []

    class Provider:
        async def get_latest_block_number(self):
            return 1_005

        async def get_logs(self, **kwargs):
            calls.append(kwargs)
            return []

    class Stream:
        settings = Settings(
            _env_file=None,
            BSC_RPC_URL="https://fixture",
            BSC_DEX_FACTORY_ADDRESSES=factory,
            BSC_QUOTE_TOKENS=quote,
            BSC_REORG_LOOKBACK_BLOCKS=1,
        )

        async def prepare_factory_candidate(self, **_kwargs):
            return None

        async def subscribe_logs(self, **_kwargs):
            return
            yield

    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        repo = AsyncStreamCheckpointRepository(session)
        for index, address in enumerate(sorted(owned | {unrelated})):
            session.add(Pair(
                chain_id=56, address=address, token0_address=token,
                token1_address=quote, quote_token=quote, factory_address=factory,
                creation_tx_hash=f"0xcreate{index}", creation_block=900 + index,
                creation_timestamp=datetime.now(UTC),
            ))
        await repo.upsert(
            chain_id=56, provider="bsc", stream_name="bsc_realtime",
            status="running", block_number=10,
        )
        await repo.upsert(
            chain_id=56, provider="bsc", stream_name=f"other_epoch:pair:{unrelated}",
            status="running", block_number=20,
        )
        if resume:
            await repo.upsert(
                chain_id=56, provider="bsc", stream_name=epoch,
                status="running", block_number=1_000,
            )
            for address, boundary in zip(sorted(owned), (950, 980), strict=True):
                await repo.upsert(
                    chain_id=56, provider="bsc", stream_name=f"{epoch}:pair:{address}",
                    status="running", block_number=boundary,
                )
        await session.commit()
        service = BSCRealtimeIngestionService(
            Stream(), session=session, chain_provider=Provider(),
            stream_name=epoch, worker_name="bsc-live-tail", start_from_head=True,
        )
        service._build_dynamic_stream = lambda *_args: service.stream
        await service.run()
        assert set(service.active_pair_registry.addresses()) == (owned if resume else set())
        assert calls[0]["from_block"] == (949 if resume else 1_004)
        pair_queries = [call for call in calls if call["address"] != (factory,)]
        assert len(pair_queries) == int(resume)
        if resume:
            assert set(pair_queries[0]["address"]) == owned
            for address in owned:
                lane = await repo.get(56, "bsc", f"{epoch}:pair:{address}")
                assert lane.last_processed_block == 1_005
    await engine.dispose()


@pytest.mark.asyncio
async def test_failed_chunk_rolls_back_before_writing_stopped_checkpoint() -> None:
    """Cleanup must not mask the first database failure with a failed transaction."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    class Stream:
        settings = Settings(_env_file=None, BSC_RPC_URL="https://fixture")

    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        service = BSCRealtimeIngestionService(Stream(), session=session)
        with pytest.raises(SQLAlchemyError):
            await session.execute(text("SELECT * FROM missing_stream_checkpoint_table"))

        await service._mark_stream_stopped()

        checkpoint = await session.scalar(
            select(StreamCheckpoint).where(StreamCheckpoint.stream_name == "bsc_realtime")
        )
        assert checkpoint is not None
        assert checkpoint.status == "stopped"
    await engine.dispose()


def test_stream_checkpoint_name_fits_live_tail_pair_lane() -> None:
    """The live-tail pair cursor includes its full canonical pair address."""
    lane = "bsc_realtime_live_tail:pair:" + "0x" + "a" * 40
    assert StreamCheckpoint.__table__.c.stream_name.type.length >= len(lane)


@pytest.mark.asyncio
async def test_duplicate_raw_without_market_projection_is_reprocessed(monkeypatch) -> None:
    """A raw replay must repair a previously failed downstream projection."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    class Stream:
        settings = Settings(
            _env_file=None,
            BSC_RPC_URL="https://fixture",
            BSC_DEX_FACTORY_ADDRESSES="0x" + "1" * 40,
        )

    calls: list[int] = []

    async def project(self, raw):
        calls.append(raw.id)
        return None, None

    monkeypatch.setattr("smms.market.pipeline.BSCMarketPipeline.process_raw_event", project)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        raw = RawChainEvent(
            chain_id=56,
            block_number=10,
            block_hash="0xblock",
            transaction_hash="0xtx",
            log_index=2,
            contract_address="0x" + "2" * 40,
            topics=["0xtopic"],
            data="0x",
            ingestion_time=datetime.now(UTC),
            provider="fixture",
            confirmation_status="confirmed",
            removed=False,
            raw_payload_json={},
        )
        session.add(raw)
        await session.commit()
        replay = ChainLog(
            address=raw.contract_address,
            topics=tuple(raw.topics),
            data=raw.data,
            block_number=raw.block_number,
            transaction_hash=raw.transaction_hash,
            log_index=raw.log_index,
            block_hash=raw.block_hash,
            confirmation_status="confirmed",
        )
        service = BSCRealtimeIngestionService(Stream(), session=session)
        await service.process_log(replay)
        assert calls == [raw.id]

    await engine.dispose()


@pytest.mark.asyncio
async def test_canonical_log_does_not_run_revalidation_before_projection(monkeypatch) -> None:
    """Normal canonical logs must not pay the expensive invalidation scan."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    class Stream:
        settings = Settings(
            _env_file=None,
            BSC_RPC_URL="https://fixture",
            BSC_DEX_FACTORY_ADDRESSES="0x" + "1" * 40,
        )

    revalidate_calls: list[int] = []

    async def revalidate(_session, raw):
        revalidate_calls.append(raw.id)

    async def project(self, _raw):
        return None, None

    monkeypatch.setattr("smms.radar.hooks.revalidate", revalidate)
    monkeypatch.setattr("smms.market.pipeline.BSCMarketPipeline.process_raw_event", project)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        service = BSCRealtimeIngestionService(Stream(), session=session)
        stored = await service.process_log(
            ChainLog(
                address="0x" + "2" * 40,
                topics=("0x" + "3" * 64,),
                data="0x",
                block_number=10,
                block_hash="0xblock",
                transaction_hash="0xtx-canonical",
                log_index=2,
                confirmation_status="confirmed",
            )
        )

    assert stored is not None
    assert revalidate_calls == []
    await engine.dispose()


@pytest.mark.asyncio
async def test_canonical_service_does_not_hold_metadata_rpc_in_the_log_transaction(monkeypatch) -> None:
    """Metadata is backfilled separately, so a slow ERC-20 call cannot stall a chunk."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    class Stream:
        settings = Settings(
            _env_file=None,
            BSC_RPC_URL="https://fixture",
            BSC_DEX_FACTORY_ADDRESSES="0x" + "1" * 40,
        )

    providers: list[object | None] = []

    async def project(self, _raw):
        providers.append(self.metadata_provider)
        return None, None

    monkeypatch.setattr("smms.market.pipeline.BSCMarketPipeline.process_raw_event", project)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        service = BSCRealtimeIngestionService(Stream(), session=session, chain_provider=object())
        await service.process_log(
            ChainLog(
                address="0x" + "2" * 40,
                topics=("0x" + "3" * 64,),
                data="0x",
                block_number=10,
                block_hash="0xblock",
                transaction_hash="0xtx-no-metadata-rpc",
                log_index=2,
                confirmation_status="confirmed",
            )
        )
    assert providers == [None]
    await engine.dispose()


@pytest.mark.asyncio
async def test_timestamp_enrichment_failure_does_not_drop_an_already_fetched_log(monkeypatch) -> None:
    """An optional block timestamp lookup cannot discard a canonical event."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    class Stream:
        settings = Settings(_env_file=None, BSC_RPC_URL="https://fixture")

    class Provider:
        async def get_block_timestamp(self, _block: int):
            raise DataUnavailable("timestamp endpoint unavailable")

    seen: list[datetime | None] = []

    async def project(self, raw):
        seen.append(raw.timestamp)
        return None, None

    monkeypatch.setattr("smms.market.pipeline.BSCMarketPipeline.process_raw_event", project)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        service = BSCRealtimeIngestionService(Stream(), session=session, chain_provider=Provider())
        stored = await service.process_log(
            ChainLog(
                address="0x" + "2" * 40,
                topics=("0x" + "3" * 64,),
                data="0x",
                block_number=10,
                block_hash="0xblock",
                transaction_hash="0xtx-timestamp-optional",
                log_index=2,
                confirmation_status="confirmed",
            )
        )
    assert stored is not None
    assert seen == [None]
    await engine.dispose()


@pytest.mark.asyncio
async def test_chunk_prefetches_timestamps_and_swap_callers_with_a_bound(monkeypatch) -> None:
    """A catch-up chunk overlaps safe RPC enrichment before serial DB writes."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    config = PancakeSwapConfig()

    class Stream:
        settings = Settings(
            _env_file=None,
            BSC_RPC_URL="https://fixture",
            BSC_CATCHUP_RPC_CONCURRENCY=2,
        )

    class Provider:
        active = 0
        maximum = 0

        async def get_block_timestamp(self, block: int):
            self.active += 1
            self.maximum = max(self.maximum, self.active)
            await asyncio.sleep(0.01)
            self.active -= 1
            return datetime(2026, 1, 1, tzinfo=UTC)

    class Resolver:
        def __init__(self) -> None:
            self.resolved = 0
            self.failed = 0
            self.calls: list[tuple[str, str | None]] = []

        async def resolve(self, tx_hash: str, *, swap_sender=None, swap_recipient=None):
            self.calls.append((tx_hash, swap_sender))
            self.resolved += 1

    def topic_address(value: int) -> str:
        return "0x" + f"{value:064x}"

    provider, resolver = Provider(), Resolver()
    logs = [
        ChainLog(
            address="0x" + "2" * 40,
            topics=(config.swap_topic, topic_address(index + 1), topic_address(index + 2)),
            data="0x" + f"{1:064x}" * 4,
            block_number=index,
            transaction_hash=f"0xtx{index}",
            log_index=index,
        )
        for index in range(1, 5)
    ]
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        service = BSCRealtimeIngestionService(Stream(), session=session, chain_provider=provider)
        service.transaction_resolver = resolver
        enriched = await service.prefetch_chunk_enrichment(logs, config)

    assert provider.maximum == 2
    assert len(resolver.calls) == 4
    assert all(log.timestamp is not None for log in enriched)
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("event_type", ["UNKNOWN", "TRADE"])
async def test_duplicate_raw_with_incomplete_market_projection_is_reprocessed(monkeypatch, event_type: str) -> None:
    """A MarketEvent row alone does not prove downstream projections are complete."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    class Stream:
        settings = Settings(_env_file=None, BSC_RPC_URL="https://fixture")

    calls: list[int] = []

    async def project(self, raw):
        calls.append(raw.id)
        return None, None

    monkeypatch.setattr("smms.market.pipeline.BSCMarketPipeline.process_raw_event", project)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        raw = RawChainEvent(
            chain_id=56, block_number=10, block_hash="0xblock", transaction_hash="0xtx",
            log_index=2, contract_address="0x" + "2" * 40, topics=["0xtopic"], data="0x",
            ingestion_time=datetime.now(UTC), provider="fixture", confirmation_status="confirmed",
            removed=False, raw_payload_json={},
        )
        session.add(raw)
        await session.flush()
        session.add(MarketEvent(
            chain_id=56, tx_hash="0xtx", event_index=2, block_number=10,
            block_hash="0xblock", event_type=event_type, pair_address=raw.contract_address,
            timestamp=raw.ingestion_time,
            source="bsc:unknown" if event_type == "UNKNOWN" else "bsc:pancakeswap",
            metadata_json={"topic0": "0xtopic", "log_index": 2},
        ))
        await session.commit()
        replay = ChainLog(
            address=raw.contract_address, topics=tuple(raw.topics), data=raw.data,
            block_number=raw.block_number, transaction_hash=raw.transaction_hash,
            log_index=raw.log_index, block_hash=raw.block_hash, confirmation_status="confirmed",
        )
        service = BSCRealtimeIngestionService(Stream(), session=session)
        await service.process_log(replay)
        assert calls == [raw.id]

    await engine.dispose()

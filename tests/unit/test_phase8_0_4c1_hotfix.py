import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from websockets.exceptions import ConnectionClosedError

from smms.chains.base import ChainLog
from smms.chains.bsc.active_pairs import ActivePairRegistry, DynamicPairSubscription
from smms.chains.bsc.websocket import (
    BSCFailoverSubscription,
    BSCHTTPPollingSubscription,
    BSCWebsocketSubscription,
)
from smms.config import Settings
from smms.db.models import Base, Pair
from smms.ingestion.bsc_stream import BSCRealtimeIngestionService
from smms.market.models import PairRecord
from smms.market.pipeline import BSCMarketPipeline
from smms.market.protocols import PancakeSwapAdapter, PancakeSwapConfig, ProtocolRegistry


def _address(value: int) -> str:
    return f"0x{value:040x}"


def _record(pair: str, factory: str, quote: str | None) -> PairRecord:
    return PairRecord(
        chain_id=56,
        address=pair,
        token0=_address(1),
        token1=quote or _address(2),
        factory=factory,
        dex="pancakeswap",
        quote_token=quote,
        creation_tx_hash="0xcreate",
        creation_block=100,
        creation_timestamp=datetime.now(UTC),
        creation_block_hash="0xblock",
    )


@pytest.mark.asyncio
async def test_factory_admission_is_scoped_and_quote_filtered() -> None:
    factory, other, quote = _address(3), _address(4), _address(5)
    registry = ActivePairRegistry()
    class Stream:
        async def subscribe_logs(self, **_kwargs):
            await asyncio.Event().wait()
            yield
    subscription = DynamicPairSubscription(
        type("Factory", (), {"settings": None})(),
        registry=registry,
        pair_stream_factory=lambda *_: Stream(),
        pair_created_decoder=lambda log: _record(_address(log.log_index), log.address, quote if log.log_index == 2 else None),
        pair_topics=("Swap",),
        factory_addresses=(factory,),
        quote_tokens=(quote,),
    )
    await subscription._handle_factory_log(ChainLog(other, ("PairCreated",), "0x", 100, "0x1", 1))
    await subscription._handle_factory_log(ChainLog(factory, ("PairCreated",), "0x", 100, "0x2", 2))
    assert registry.count == 1
    assert registry.get(_address(2)) is not None
    await subscription.close()


@pytest.mark.asyncio
async def test_factory_exception_is_surfaceable_instead_of_silent_queue_wait() -> None:
    class Factory:
        settings = None

        async def subscribe_logs(self, **_kwargs):
            raise RuntimeError("connection closed")
            yield

    subscription = DynamicPairSubscription(
        Factory(), registry=ActivePairRegistry(), pair_stream_factory=lambda *_: None,
        pair_created_decoder=lambda _log: None, pair_topics=("PairCreated",),
        refresh_interval_seconds=0.01,
    )
    iterator = subscription.subscribe_logs().__aiter__()
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(iterator.__anext__(), timeout=1)
    assert subscription._factory_error is not None


@pytest.mark.asyncio
async def test_pair_task_restart_allocates_unset_readiness_generation() -> None:
    pair = _address(20)
    registry = ActivePairRegistry()
    registry.add(_record(pair, _address(3), _address(5)))

    class Stream:
        async def subscribe_logs(self, **_kwargs):
            await asyncio.Event().wait()
            yield

    subscription = DynamicPairSubscription(
        type("Factory", (), {"settings": None})(), registry=registry,
        pair_stream_factory=lambda *_: Stream(), pair_created_decoder=lambda _log: None,
        pair_topics=("Swap",),
    )
    await subscription._ensure_pair_tasks()
    first = subscription._tasks[next(iter(subscription._tasks))]
    old_event = subscription._ready_events[next(iter(subscription._tasks))]
    first.cancel()
    await asyncio.gather(first, return_exceptions=True)
    await subscription._ensure_pair_tasks()
    key = next(iter(subscription._tasks))
    assert subscription._tasks[key] is not first
    assert subscription._ready_events[key] is not old_event
    assert subscription._ready_events[key].is_set()
    await subscription.close()


@pytest.mark.asyncio
async def test_http_factory_polling_keeps_configured_address_filter() -> None:
    calls = []
    log = ChainLog(_address(3), ("PairCreated",), "0x", 10, "0xtx", 0)

    class Provider:
        async def get_latest_block_number(self):
            return 10

        async def get_logs(self, **kwargs):
            calls.append(kwargs)
            return [log]

    holder = {}

    async def stop(_delay):
        holder["stream"]._closed = True

    settings = Settings(_env_file=None, BSC_POLL_INTERVAL_SECONDS=0.01)
    stream = BSCHTTPPollingSubscription(Provider(), settings, sleep=stop)
    holder["stream"] = stream
    values = [item async for item in stream.subscribe_logs(address=(_address(3), _address(4)), topics=("PairCreated",))]
    assert values == [log]
    assert calls[0]["address"] == (_address(3), _address(4))


@pytest.mark.asyncio
async def test_http_catchup_queries_pair_logs_only_for_quote_eligible_pairs(monkeypatch) -> None:
    factory, quote = _address(3), _address(5)
    eligible, ineligible = _address(20), _address(21)
    factory_logs = [
        ChainLog(factory, ("PairCreated",), "0x", 100, "0xeligible", 1),
        ChainLog(factory, ("PairCreated",), "0x", 100, "0xineligible", 2),
    ]
    calls = []

    class Provider:
        async def get_latest_block_number(self):
            return 100

        async def get_logs(self, **kwargs):
            calls.append(kwargs)
            return factory_logs if len(calls) == 1 else []

    class Stream:
        settings = Settings(
            _env_file=None,
            BSC_RPC_URL="https://fixture",
            BSC_DEX_FACTORY_ADDRESSES=factory,
            BSC_QUOTE_TOKENS=quote,
            BSC_REORG_LOOKBACK_BLOCKS=0,
        )

        async def prepare_factory_candidate(self, **_kwargs):
            return None

        async def subscribe_logs(self, **_kwargs):
            return
            yield

    def decode(_self, log):
        return _record(
            eligible if log.log_index == 1 else ineligible,
            factory,
            quote if log.log_index == 1 else None,
        )

    monkeypatch.setattr(PancakeSwapAdapter, "detect", lambda _self, _log: True)
    monkeypatch.setattr(PancakeSwapAdapter, "decode_pair_created", decode)

    async def no_op(_value):
        return None

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        service = BSCRealtimeIngestionService(Stream(), session=session, chain_provider=Provider())
        service._build_dynamic_stream = lambda *_args: service.stream
        service.process_log = no_op
        service.confirm_through = no_op
        await service.run()
    await engine.dispose()

    assert len(calls) == 2
    assert calls[1]["address"] == (eligible,)


@pytest.mark.asyncio
async def test_http_catchup_keeps_eligible_pair_in_later_chunk(monkeypatch) -> None:
    """A pair discovered in chunk N remains queryable in chunk N+1."""
    factory, quote = _address(3), _address(5)
    eligible = _address(20)
    created = ChainLog(factory, ("PairCreated",), "0x", 100, "0xcreated", 1)
    swap = ChainLog(eligible, ("Swap",), "0x", 101, "0xswap", 1)
    calls = []

    class Provider:
        async def get_latest_block_number(self):
            return 101

        async def get_logs(self, **kwargs):
            calls.append(kwargs)
            if kwargs["address"] == (factory,):
                return [created] if kwargs["from_block"] <= 100 <= kwargs["to_block"] else []
            return [swap] if kwargs["from_block"] <= 101 <= kwargs["to_block"] else []

    class Stream:
        settings = Settings(
            _env_file=None,
            BSC_RPC_URL="https://fixture",
            BSC_DEX_FACTORY_ADDRESSES=factory,
            BSC_QUOTE_TOKENS=quote,
            BSC_REORG_LOOKBACK_BLOCKS=2,
            BSC_LOG_CHUNK_SIZE=1,
        )

        async def prepare_factory_candidate(self, **_kwargs):
            return None

        async def subscribe_logs(self, **_kwargs):
            return
            yield

    def decode(_self, _log):
        return _record(eligible, factory, quote)

    monkeypatch.setattr(PancakeSwapAdapter, "detect", lambda _self, _log: True)
    monkeypatch.setattr(PancakeSwapAdapter, "decode_pair_created", decode)

    async def process(log):
        if log.address == factory:
            service.active_pair_registry.add(decode(None, log))

    async def no_op(_value):
        return None

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        service = BSCRealtimeIngestionService(Stream(), session=session, chain_provider=Provider())
        service._build_dynamic_stream = lambda *_args: service.stream
        service.process_log = process
        service.confirm_through = no_op
        await service.run()
    await engine.dispose()

    later_pair_calls = [
        call for call in calls
        if call["address"] == (eligible,) and call["from_block"] <= 101 <= call["to_block"]
    ]
    assert later_pair_calls


class _Candidate:
    def __init__(self, values, error=None):
        self.values = list(values)
        self.error = error
        self.queue = asyncio.Queue()
        self.closed = False
        self.endpoint_index = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.values:
            return self.values.pop(0)
        if self.error is not None:
            error, self.error = self.error, None
            raise error
        await asyncio.Event().wait()

    def drain_buffer(self):
        return []

    async def close(self):
        self.closed = True


class _OwnerWS:
    provider = "bsc_websocket"

    def __init__(self, candidate):
        self.settings = Settings(_env_file=None, BSC_WS_URL="wss://fixture")
        self.metrics = type("Metrics", (), {
            "current_block": 0, "event_count": 0, "reconnect_count": 0,
            "provider_errors": 0, "decode_failures": 0,
            "ingestion_latency_ms": 0, "last_event_at": None,
        })()
        self.candidate = candidate

    async def open_log_candidate(self, **_kwargs):
        return self.candidate

    async def subscribe_logs(self, **_kwargs):
        raise AssertionError("owner must consume committed candidate")
        yield

    async def probe_health(self):
        return False

    def mark_endpoint_failure(self, *_args, **_kwargs):
        return None

    def set_start_block(self, _value):
        return None

    async def close(self):
        return None


class _OwnerHTTP:
    provider = "bsc_http_polling"

    def __init__(self, values):
        self.rpc_provider = self
        self.values = list(values)
        self.metrics = type("Metrics", (), {
            "current_block": 200, "event_count": 0, "reconnect_count": 0,
            "provider_errors": 0, "decode_failures": 0,
            "ingestion_latency_ms": 0, "last_event_at": None,
        })()

    async def get_latest_block_number(self):
        return 200

    async def get_logs(self, **_kwargs):
        return []

    def set_start_block(self, _value):
        return None

    async def subscribe_logs(self, **_kwargs):
        for value in self.values:
            yield value
        await asyncio.Event().wait()

    async def close(self):
        return None


@pytest.mark.asyncio
async def test_prepared_factory_candidate_disconnect_falls_back_without_dynamic_exit() -> None:
    factory = _address(3)
    ws_log = ChainLog(factory, ("PairCreated",), "0x", 100, "0xws", 0)
    http_log = ChainLog(factory, ("PairCreated",), "0x", 101, "0xhttp", 0)
    candidate = _Candidate([ws_log], ConnectionClosedError(None, None))
    owner = BSCFailoverSubscription(_OwnerWS(candidate), _OwnerHTTP([http_log]))
    dynamic = DynamicPairSubscription(
        owner, registry=ActivePairRegistry(), pair_stream_factory=lambda *_: None,
        pair_created_decoder=lambda _log: None, pair_topics=("PairCreated",),
        factory_addresses=(factory,),
    )
    await dynamic.prepare_factory_candidate(address=(factory,), topics=("PairCreated",))
    iterator = dynamic.subscribe_logs(address=(factory,), topics=("PairCreated",)).__aiter__()
    assert (await iterator.__anext__()).transaction_hash == "0xws"
    assert (await iterator.__anext__()).transaction_hash == "0xhttp"
    assert owner.active_transport == "http"
    await dynamic.close()


@pytest.mark.asyncio
async def test_pair_supervisor_retries_after_transport_failure() -> None:
    pair = _address(30)
    registry = ActivePairRegistry()
    registry.add(_record(pair, _address(3), _address(5)))
    attempts = 0
    recovered = asyncio.Event()

    class Broken:
        async def subscribe_logs(self, **_kwargs):
            raise ConnectionClosedError(None, None)
            yield

    class Recovered:
        async def subscribe_logs(self, **_kwargs):
            recovered.set()
            await asyncio.Event().wait()
            yield

    def make_stream(*_args):
        nonlocal attempts
        attempts += 1
        return Broken() if attempts == 1 else Recovered()

    dynamic = DynamicPairSubscription(
        type("Factory", (), {"settings": None})(), registry=registry,
        pair_stream_factory=make_stream, pair_created_decoder=lambda _log: None,
        pair_topics=("Swap",), refresh_interval_seconds=0.01,
    )
    refresh = asyncio.create_task(dynamic._refresh_pairs())
    await asyncio.wait_for(recovered.wait(), timeout=1)
    assert not refresh.done()
    await dynamic.close()


@pytest.mark.asyncio
async def test_prepared_pair_candidate_disconnect_falls_back_to_http_owner() -> None:
    pair = _address(30)
    ws_log = ChainLog(pair, ("Swap",), "0x", 100, "0xpair-ws", 0)
    http_log = ChainLog(pair, ("Swap",), "0x", 101, "0xpair-http", 0)
    owner = BSCFailoverSubscription(
        _OwnerWS(_Candidate([ws_log], ConnectionClosedError(None, None))),
        _OwnerHTTP([http_log]),
    )
    candidate = await owner.prepare_log_candidate(address=(pair,), topics=("Swap",))
    assert candidate is not None
    owner.commit_prepared_candidate(candidate)
    output = []
    async for log in owner.subscribe_logs(address=(pair,), topics=("Swap",)):
        output.append(log)
        if len(output) == 2:
            await owner.close()
    assert [log.transaction_hash for log in output] == ["0xpair-ws", "0xpair-http"]
    assert owner.active_transport == "http"


@pytest.mark.asyncio
async def test_configured_non_quote_pair_is_persisted_but_not_live_admitted() -> None:
    factory = _address(3)
    config = PancakeSwapConfig(factory_addresses=(factory,), quote_assets=(_address(5),))
    adapter = PancakeSwapAdapter(config)

    def word(value: int) -> str:
        return f"{value:064x}"

    raw = type("Raw", (), {
        "removed": False, "confirmation_status": "pending", "contract_address": factory,
        "topics": [config.pair_created_topic, "0x" + word(1), "0x" + word(2)],
        "data": "0x" + word(9) + word(1), "block_number": 100,
        "transaction_hash": "0xnonquote", "transaction_index": 0, "log_index": 0,
        "block_hash": "0xblock", "timestamp": datetime.now(UTC),
        "raw_payload_json": {}, "id": 1, "chain_id": 56,
    })()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        registry = ActivePairRegistry()
        await BSCMarketPipeline(
            session, registry=ProtocolRegistry((adapter,)), active_pair_registry=registry,
        ).process_raw_event(raw)
        assert await session.scalar(select(Pair).where(Pair.address == _address(9))) is not None
        assert registry.count == 0
    await engine.dispose()


@pytest.mark.asyncio
async def test_websocket_handshake_passes_multi_factory_address_filter() -> None:
    class Socket:
        def __init__(self):
            self.sent = []

        async def send(self, value):
            self.sent.append(json.loads(value))

        async def recv(self):
            request = self.sent[-1]
            result = "0x38" if request["method"] == "eth_chainId" else "sub"
            return json.dumps({"result": result})

    socket = Socket()
    stream = BSCWebsocketSubscription(Settings(_env_file=None, BSC_WS_URL="wss://fixture"))
    await stream._handshake_logs(
        socket, address=(_address(3), _address(4)), topics=("PairCreated",)
    )
    assert socket.sent[-1]["params"][1]["address"] == [_address(3), _address(4)]


@pytest.mark.asyncio
async def test_unexpected_refresh_exit_surfaces_to_main_queue() -> None:
    class Factory:
        settings = None

        async def subscribe_logs(self, **_kwargs):
            await asyncio.Event().wait()
            yield

    dynamic = DynamicPairSubscription(
        Factory(), registry=ActivePairRegistry(), pair_stream_factory=lambda *_: None,
        pair_created_decoder=lambda _log: None, pair_topics=("PairCreated",),
        refresh_interval_seconds=0.01,
    )
    iterator = dynamic.subscribe_logs().__aiter__()
    pending = asyncio.create_task(iterator.__anext__())
    await asyncio.sleep(0)
    old = dynamic._refresh_task
    assert old is not None
    old.cancel()
    await asyncio.gather(old, return_exceptions=True)

    async def fatal():
        raise RuntimeError("refresh fatal")

    dynamic._refresh_task = asyncio.create_task(fatal())
    await asyncio.sleep(0.02)
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(pending, timeout=1)
    assert isinstance(dynamic._refresh_error, RuntimeError)


@pytest.mark.asyncio
async def test_reorg_reconciliation_removes_registry_orphan_and_commits() -> None:
    pair_address, factory = _address(40), _address(3)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    class Provider:
        async def get_block(self, _number, *, include_transactions=False):
            return type("Block", (), {"block_hash": "0xhash-b"})()

    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        session.add(Pair(
            chain_id=56, address=pair_address, token0_address=_address(1),
            token1_address=_address(5), factory_address=factory, quote_token=_address(5),
            creation_tx_hash="0xcreate-a", creation_block=100,
            creation_block_hash="0xhash-a", canonical=True, removed=False,
            creation_timestamp=datetime.now(UTC),
        ))
        await session.commit()
        registry = ActivePairRegistry()
        registry.add(_record(pair_address, factory, _address(5)))
        service = BSCRealtimeIngestionService(
            type("Stream", (), {"settings": Settings(_env_file=None, BSC_REORG_LOOKBACK_BLOCKS=12)})(),
            session=session, chain_provider=Provider(), active_pair_registry=registry,
        )
        assert await service.reconcile_reorg_window(100, 12) == 1
        pair = await session.scalar(select(Pair).where(Pair.address == pair_address))
        assert pair is not None and pair.canonical is False and pair.removed is True
        assert registry.get(pair_address) is None
        await service.close()
    await engine.dispose()


def test_compose_bsc_ingestion_does_not_require_redis() -> None:
    import yaml

    root = Path(__file__).parents[2]
    compose = yaml.safe_load((root / "docker-compose.yml").read_text(encoding="utf-8"))
    assert "redis" not in compose["services"]["bsc-ingestion"].get("depends_on", {})


def test_factory_ws_filter_contains_every_configured_address() -> None:
    addresses = (_address(3), _address(4))
    assert BSCWebsocketSubscription._filter(addresses, ("PairCreated",)) == {
        "address": list(addresses),
        "topics": ["PairCreated"],
    }

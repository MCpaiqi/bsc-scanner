from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from smms import runtime
from smms.chains.base import ChainLog
from smms.chains.bsc.active_pairs import ActivePairRegistry, DynamicPairSubscription
from smms.chains.bsc.websocket import BlockWindowDedupe
from smms.db.models import Base, Pair, RawChainEvent, StreamCheckpoint
from smms.ingestion.bsc_stream import BSCRealtimeIngestionService
from smms.main import _finalize_live_bsc_readiness
from smms.market.models import PairRecord
from smms.market.pipeline import BSCMarketPipeline
from smms.market.protocols import PancakeSwapAdapter, PancakeSwapConfig, ProtocolRegistry


def address(value: int) -> str:
    return f"0x{value:040x}"


def record(pair: str, at: datetime, block: int = 100) -> PairRecord:
    return PairRecord(
        chain_id=56, address=pair, token0=address(1), token1=address(2),
        factory=address(3), dex="pancakeswap", quote_token=address(2),
        creation_tx_hash=f"0xcreate{block}", creation_block=block,
        creation_timestamp=at, creation_block_hash=f"0xfork{block}",
    )


def test_live_eviction_and_touch_use_recency_before_default_priority() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    registry = ActivePairRegistry(max_active_pairs=2, clock=lambda: start + timedelta(hours=1))
    registry.add(record(address(10), start))
    registry.add(record(address(11), start + timedelta(minutes=1)))
    registry.add(record(address(12), start + timedelta(minutes=2)))
    registry.touch(address(12), start + timedelta(minutes=10))
    registry.add(record(address(13), start + timedelta(minutes=3)))
    assert set(registry.addresses()) == {address(12), address(13)}
    assert registry.add(record(address(14), start - timedelta(days=1))) is None
    assert set(registry.addresses()) == {address(12), address(13)}


def test_block_window_dedupe_prunes_by_blocks_and_suppresses_handoff_duplicate() -> None:
    dedupe = BlockWindowDedupe(5)
    old = ChainLog(address=address(1), topics=(), data="0x", block_number=1,
                   transaction_hash="0xold", log_index=0, block_hash="0xa")
    recent = ChainLog(address=address(1), topics=(), data="0x", block_number=10,
                      transaction_hash="0xrecent", log_index=0, block_hash="0xb")
    assert dedupe.accept(old)
    assert dedupe.accept(recent)
    assert len(dedupe) == 1
    assert not dedupe.accept(recent)


def test_wrong_chain_reason_is_blocking_even_if_rpc_result_says_56() -> None:
    payload = {"chain_id": 56, "database": "OK", "migration": {"ready": True},
               "rpc": "OK", "factory": [{"deployed": True}], "redis": "OK"}
    _finalize_live_bsc_readiness(payload, ["WRONG_CHAIN"])
    assert payload["ingestion_ready"] is False


@pytest.mark.asyncio
async def test_market_enrichment_cannot_overwrite_ingestion_running(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    async def capture(worker_type: str, status: str, **_kwargs) -> None:
        calls.append((worker_type, status))

    monkeypatch.setattr(runtime, "heartbeat", capture)
    await runtime.market_enrichment_heartbeat(count=2)
    assert calls == [("bsc-market-enrichment", "RUNNING")]


def test_bsc_service_failure_is_explicitly_degraded() -> None:
    details = runtime.bsc_service_failure_details(
        RuntimeError("endpoint unavailable"), transport="http"
    )
    assert details["reason"] == "SERVICE_CYCLE_FAILED"
    assert details["active_transport"] == "http"
    assert details["ingestion_ready"] is False
    assert details["last_error"] == "RuntimeError: endpoint unavailable"


def test_backfill_and_live_tail_have_distinct_runtime_identity_and_env_file() -> None:
    """Separate BSC lanes cannot collide in the heartbeat uniqueness key."""
    import yaml

    compose = yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))
    backfill = compose["services"]["bsc-ingestion"]
    tail = compose["services"]["bsc-live-tail"]
    assert backfill["environment"]["SMMS_INSTANCE_ID"] == "bsc-backfill"
    assert tail["environment"]["SMMS_INSTANCE_ID"] == "bsc-live-tail"
    assert tail["env_file"] == [{"path": ".env", "required": False}]


@pytest.mark.asyncio
async def test_factory_candidate_is_acked_and_buffered_before_primary_subscription() -> None:
    factory_log = ChainLog(address=address(3), topics=("PairCreated",), data="0x",
                           block_number=101, transaction_hash="0xnew", log_index=0)

    class Candidate:
        def __init__(self):
            self.items = [factory_log]

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self.items:
                return self.items.pop(0)
            await asyncio.Event().wait()

    class Factory:
        provider = "fixture"
        settings = None

        async def open_log_candidate(self, **_kwargs):
            return Candidate()

        async def subscribe_logs(self, **_kwargs):
            raise AssertionError("prepared candidate must become primary")
            yield

    dynamic = DynamicPairSubscription(
        Factory(), registry=ActivePairRegistry(), pair_stream_factory=lambda *_: None,
        pair_created_decoder=lambda _log: None, pair_topics=("Swap",),
    )
    await dynamic.prepare_factory_candidate(topics=("PairCreated",))
    iterator = dynamic.subscribe_logs(topics=("PairCreated",)).__aiter__()
    assert (await iterator.__anext__()).transaction_hash == "0xnew"
    await dynamic.close()


@pytest.mark.asyncio
async def test_factory_startup_handoff_captures_block_101_once_after_barrier_100() -> None:
    created = ChainLog(address=address(3), topics=("PairCreated",), data="0x",
                       block_number=101, transaction_hash="0xhandoff", log_index=0)
    acked = False

    class Candidate:
        delivered = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self.delivered:
                self.delivered = True
                return created
            raise RuntimeError("finite fixture")

    class Factory:
        provider = "fixture"
        settings = None

        async def open_log_candidate(self, **_kwargs):
            nonlocal acked
            acked = True
            return Candidate()

    class Provider:
        async def get_latest_block_number(self):
            assert acked
            return 100

        async def get_logs(self, **_kwargs):
            return []

    dynamic = DynamicPairSubscription(
        Factory(), registry=ActivePairRegistry(), pair_stream_factory=lambda *_: None,
        pair_created_decoder=lambda _log: None, pair_topics=("Swap",),
    )
    stream_settings = type("Settings", (), {
        "bsc_dex_factory_addresses": address(3), "bsc_quote_tokens": address(2),
        "bsc_reorg_lookback_blocks": 12, "bsc_log_chunk_size": 100,
    })()
    dynamic.settings = stream_settings
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        service = BSCRealtimeIngestionService(dynamic, session=session, chain_provider=Provider())
        service._build_dynamic_stream = lambda *_args: dynamic
        await service.run()
        # Exercise the real owned transaction; helper mocks would bypass the
        # persistence/cancellation race this handoff must survive.
        rows = (await session.scalars(select(RawChainEvent))).all()
        checkpoint = await session.scalar(select(StreamCheckpoint).where(
            StreamCheckpoint.stream_name == "bsc_realtime",
        ))
        assert [row.transaction_hash for row in rows] == ["0xhandoff"]
        assert checkpoint.last_processed_block == 101
        assert checkpoint.status == "stopped"
    await engine.dispose()


@pytest.mark.asyncio
async def test_pair_activation_waits_for_ack_then_replays_same_block_and_drains_buffer() -> None:
    pair_address = address(30)
    created = ChainLog(address=address(3), topics=("PairCreated",), data="0x",
                       block_number=100, transaction_hash="0xcreate", log_index=10)
    same_block = ChainLog(address=pair_address, topics=("Swap",), data="0x",
                          block_number=100, transaction_hash="0xswap100", log_index=20)
    buffered = ChainLog(address=pair_address, topics=("Swap",), data="0x",
                        block_number=101, transaction_hash="0xswap101", log_index=1)
    acked = False

    class Factory:
        provider = "fixture"
        settings = None

        async def subscribe_logs(self, **_kwargs):
            yield created

    class PairCandidate:
        sent = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self.sent:
                self.sent = True
                return buffered
            await asyncio.Event().wait()

        async def close(self):
            return None

    class PairSource:
        async def open_log_candidate(self, **_kwargs):
            nonlocal acked
            acked = True
            return PairCandidate()

    class Provider:
        async def get_latest_block_number(self):
            assert acked
            return 101

        async def get_logs(self, **_kwargs):
            return [same_block, buffered]

    dynamic = DynamicPairSubscription(
        Factory(), registry=ActivePairRegistry(clock=lambda: datetime.now(UTC)),
        pair_stream_factory=lambda *_: PairSource(),
        pair_created_decoder=lambda _log: record(pair_address, datetime.now(UTC)),
        pair_topics=("Swap",), activation_replay_provider=Provider(),
    )
    iterator = dynamic.subscribe_logs().__aiter__()
    received = [await iterator.__anext__() for _ in range(3)]
    assert [item.transaction_hash for item in received] == [
        "0xcreate", "0xswap100", "0xswap101",
    ]
    await dynamic.close()


async def _append_async(target: list[str], value: str):
    target.append(value)


async def _return_async(value):
    return value


@pytest.mark.asyncio
async def test_pair_created_fork_removal_replacement_and_bootstrap_exclusion() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory, pair_address = address(3), address(9)
    config = PancakeSwapConfig(factory_addresses=(factory,), quote_assets=(address(2),))
    adapter = PancakeSwapAdapter(config)

    def word(value: int) -> str:
        return f"{value:064x}"

    def raw(block_hash: str | None, *, removed: bool = False):
        return type("Raw", (), {
            "removed": removed, "confirmation_status": "orphaned" if removed else "pending",
            "contract_address": factory, "topics": [config.pair_created_topic,
                "0x" + word(1), "0x" + word(2)],
            "data": "0x" + word(int(pair_address, 16)) + word(1), "block_number": 100,
            "transaction_hash": "0xcreate", "transaction_index": 0, "log_index": 10,
            "block_hash": block_hash, "timestamp": datetime.now(UTC),
            "raw_payload_json": {}, "id": 999, "chain_id": 56,
        })()

    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        registry = ActivePairRegistry(clock=lambda: datetime.now(UTC))
        pipeline = BSCMarketPipeline(
            session, registry=ProtocolRegistry((adapter,)), active_pair_registry=registry
        )
        await pipeline.process_raw_event(raw("0xfork-a"))
        await pipeline.process_raw_event(raw("0xfork-a", removed=True))
        pair = await session.scalar(select(Pair).where(Pair.address == pair_address))
        assert pair is not None and not pair.canonical and pair.removed
        assert registry.get(pair_address) is None
        restored = ActivePairRegistry(clock=lambda: datetime.now(UTC))
        await restored.bootstrap(session)
        assert restored.get(pair_address) is None
        await pipeline.process_raw_event(raw("0xfork-b"))
        assert pair.canonical and not pair.removed and pair.creation_block_hash == "0xfork-b"
        assert registry.get(pair_address) is not None
    await engine.dispose()


@pytest.mark.asyncio
async def test_removed_pair_created_without_block_hash_fails_closed() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    pair_address = address(20)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        pair = Pair(chain_id=56, address=pair_address, token0_address=address(1),
                    token1_address=address(2), creation_tx_hash="0xcreate",
                    creation_block=100, creation_block_hash="0xa", canonical=True, removed=False)
        session.add(pair)
        await session.flush()
        removed = type("Removed", (), {"block_hash": None, "transaction_hash": "0xcreate",
                                        "log_index": 10, "raw_payload_json": {}})()
        await BSCMarketPipeline(session)._invalidate_event(removed)
        assert pair.canonical and not pair.removed
        assert removed.raw_payload_json["reorg_evidence"] == "UNRESOLVED_MISSING_BLOCK_HASH"
    await engine.dispose()

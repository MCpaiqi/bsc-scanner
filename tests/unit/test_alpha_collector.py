"""Real transactions with fake confirmed RPC; optionally isolated PostgreSQL."""

import asyncio
import os
import uuid
from datetime import timedelta

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test_alpha_coverage_integrity import FACTORY, PAIR, T0, pair

from smms.alpha.collector import claim_work, fetch_scan, persist_scan, retry_claim
from smms.alpha.work import SWAP_TOPIC
from smms.chains.base import Block, ChainLog, ChainTransaction
from smms.db.models import (
    AlphaCoverageJob,
    AlphaCoverageReceipt,
    Base,
    MarketEvent,
    RawChainEvent,
    TradeEvent,
)
from smms.evaluation import utc

NOW = T0 + timedelta(seconds=4000)


@pytest_asyncio.fixture
async def coverage_session():
    url = make_url(os.getenv("SMMS_RESEARCH_TEST_DATABASE_URL", "sqlite+aiosqlite:///:memory:"))
    schema = "smms_alpha_" + uuid.uuid4().hex if url.get_backend_name() == "postgresql" else None
    if schema:
        url = url.set(drivername="postgresql+asyncpg")
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            if schema:
                await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
                await connection.execute(text(f'SET search_path TO "{schema}"'))
            await connection.run_sync(Base.metadata.create_all)
        options = {"schema_translate_map": {None: schema}} if schema else {}
        scoped = engine.execution_options(**options)
        async with async_sessionmaker(scoped, expire_on_commit=False)() as session:
            await session.run_sync(pair)
            await session.commit()
            yield session
    finally:
        if schema:
            async with engine.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await engine.dispose()


class Provider:
    def __init__(self, *, empty=False, fail=False, changed=False, malformed=False):
        self.empty, self.fail, self.changed, self.malformed = empty, fail, changed, malformed
        self.queries = []
        self.reads = 0

    async def get_latest_block_number(self):
        return 102

    async def get_block(self, number):
        if number == 101:
            self.reads += 1
        return Block(number=number,
                     block_hash="fork" if self.changed and self.reads > 1 else f"0xblock{number}",
                     timestamp=T0 + timedelta(seconds=0 if number == 100 else 4000),
                     raw={"parentHash": f"0xblock{number - 1}"})

    async def get_logs(self, **kwargs):
        self.queries.append(kwargs)
        if self.fail:
            raise OSError("synthetic RPC failure")
        if self.empty:
            return []
        wallet = "0x" + "5" * 40
        return [ChainLog(address=PAIR, topics=(SWAP_TOPIC, "0x" + wallet[2:].zfill(64),
                                             "0x" + wallet[2:].zfill(64)),
                         data="0xBAD" if self.malformed else "0x" + "".join(
                             f"{value:064x}" for value in (0, 2 * 10**18, 10**18, 0)),
                         block_number=100, transaction_hash="0xtx", log_index=0,
                         block_hash="0xblock100")]

    async def get_transaction(self, tx):
        return ChainTransaction(transaction_hash=tx, block_number=100, from_address="0x" + "5" * 40,
                                to_address=PAIR)


async def claim(session):
    result = await session.run_sync(lambda sync: claim_work(sync, now=NOW, factories={FACTORY}))
    await session.commit()
    assert result is not None
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("empty", [False, True])
async def test_collector_commits_receipt_with_all_canonical_facts(coverage_session, empty):
    session = coverage_session
    item = await claim(session)
    provider = Provider(empty=empty)
    scan = await fetch_scan(provider, item, confirmations=1, chunk_size=10)
    now = NOW + timedelta(seconds=1)
    assert await persist_scan(session, item, scan, now=now)
    await session.commit()
    receipt = await session.scalar(select(AlphaCoverageReceipt))
    assert receipt.raw_count == receipt.decoded_count == (0 if empty else 1)
    for model in (RawChainEvent, MarketEvent, TradeEvent):
        assert await session.scalar(select(func.count()).select_from(model)) == (0 if empty else 1)
    job = await session.get(AlphaCoverageJob, item.job_id)
    assert job.status == "COMPLETE" and job.next_block == 102
    assert provider.queries == [{"from_block": 100, "to_block": 101, "address": PAIR, "topics": [SWAP_TOPIC]}]
    assert not await persist_scan(session, item, scan, now=now)  # stale replay


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["fail", "changed", "malformed"])
async def test_failed_query_leaves_no_receipt_and_durable_retry(coverage_session, defect):
    session = coverage_session
    item = await claim(session)
    with pytest.raises((OSError, ValueError)):
        await fetch_scan(Provider(**{defect: True}), item, confirmations=1, chunk_size=10)
    await session.run_sync(lambda sync: retry_claim(sync, item, now=NOW, error="TEST_FAILURE"))
    await session.commit()
    job = await session.get(AlphaCoverageJob, item.job_id)
    assert job.last_error == "TEST_FAILURE" and job.next_block == 100
    assert job.lease_token is None and utc(job.next_attempt_at) > NOW
    assert await session.scalar(select(func.count()).select_from(AlphaCoverageReceipt)) == 0


@pytest.mark.asyncio
async def test_projection_rollback_cannot_leave_a_coverage_receipt(coverage_session):
    session = coverage_session
    item = await claim(session)
    scan = await fetch_scan(Provider(), item, confirmations=1, chunk_size=10)
    assert await persist_scan(session, item, scan, now=NOW + timedelta(seconds=1))
    await session.rollback()
    for model in (AlphaCoverageReceipt, RawChainEvent, MarketEvent, TradeEvent):
        assert await session.scalar(select(func.count()).select_from(model)) == 0
    job = await session.get(AlphaCoverageJob, item.job_id)
    assert job.next_block == 100


@pytest.mark.asyncio
async def test_generation_change_rejects_in_flight_scan(coverage_session):
    session = coverage_session
    item = await claim(session)
    scan = await fetch_scan(Provider(), item, confirmations=1, chunk_size=10)
    job = await session.get(AlphaCoverageJob, item.job_id)
    job.generation += 1
    await session.commit()
    assert not await persist_scan(session, item, scan, now=NOW + timedelta(seconds=1))
    assert await session.scalar(select(func.count()).select_from(RawChainEvent)) == 0


@pytest.mark.asyncio
async def test_empty_receipt_fork_is_invalidated_by_ingestion_reconciler(coverage_session):
    from types import SimpleNamespace

    from smms.config import Settings
    from smms.ingestion.bsc_stream import BSCRealtimeIngestionService

    session = coverage_session
    item = await claim(session)
    scan = await fetch_scan(Provider(empty=True), item, confirmations=1, chunk_size=10)
    await persist_scan(session, item, scan, now=NOW + timedelta(seconds=1))
    await session.commit()

    class Fork:
        async def get_block(self, number, *, include_transactions=False):
            return Block(number=number, block_hash="newfork", timestamp=NOW)

    service = BSCRealtimeIngestionService(SimpleNamespace(settings=Settings(_env_file=None)),
                                        session=session, chain_provider=Fork())
    await service.reconcile_reorg_window(101, 1)
    receipt = await session.scalar(select(AlphaCoverageReceipt))
    assert receipt.canonical is False
    job = await session.get(AlphaCoverageJob, item.job_id)
    assert job.generation == 1 and job.next_block == 100


@pytest.mark.asyncio
async def test_slow_rpc_retains_independent_worker_heartbeat(monkeypatch):
    from smms import runtime

    release = asyncio.Event()
    calls = []

    async def slow(*_args, **_kwargs):
        await release.wait()
        return "scan"

    async def beat(*args, **kwargs):
        calls.append((args, kwargs))
        release.set()

    monkeypatch.setattr(runtime, "heartbeat", beat)
    result = await asyncio.wait_for(runtime._alpha_scan_with_heartbeat(
        slow, None, type("Claim", (), {"job_id": 1, "start": 100, "generation": 0})(),
        confirmations=1, chunk_size=10, interval=0.01,
    ), timeout=1)
    assert result == "scan" and calls[0][0] == ("alpha-coverage", "RUNNING")


@pytest.mark.asyncio
async def test_first_empty_scan_cannot_commit_after_reconciliation(coverage_session):
    from types import SimpleNamespace

    from smms.config import Settings
    from smms.ingestion.bsc_stream import BSCRealtimeIngestionService

    session = coverage_session
    item = await claim(session)
    scan = await fetch_scan(Provider(empty=True), item, confirmations=1, chunk_size=10)

    class Fork:
        async def get_block(self, number, *, include_transactions=False):
            return Block(number=number, block_hash="newfork" if number == 101 else "0xblock100", timestamp=NOW)

    service = BSCRealtimeIngestionService(SimpleNamespace(settings=Settings(_env_file=None)),
                                        session=session, chain_provider=Fork())
    await service.reconcile_reorg_window(101, 1)
    assert not await persist_scan(session, item, scan, now=NOW + timedelta(seconds=1))
    assert await session.scalar(select(func.count()).select_from(AlphaCoverageReceipt)) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [None, [None], [42]])
async def test_malformed_rpc_response_is_not_an_empty_success(monkeypatch, payload):
    from smms.chains.bsc.rpc import BSCProvider
    from smms.config import Settings
    from smms.ingestion import DataUnavailable

    provider = BSCProvider(Settings(_env_file=None), rpc_urls=["https://synthetic.invalid"])

    async def request(*_args):
        return payload

    monkeypatch.setattr(provider, "_request", request)
    with pytest.raises(DataUnavailable, match="logs"):
        await provider.get_logs(from_block=100, to_block=101, address=PAIR, topics=[SWAP_TOPIC])


@pytest.mark.asyncio
async def test_canonical_rediscovery_resumes_unscored_work(coverage_session):
    from smms.alpha.work import invalidate_coverage, register_pair
    from smms.db.models import AlphaSnapshot, Pair

    session = coverage_session
    item = await claim(session)
    scan = await fetch_scan(Provider(empty=True), item, confirmations=1, chunk_size=10)
    await persist_scan(session, item, scan, now=NOW + timedelta(seconds=1))
    p = await session.get(Pair, item.pair_id)
    p.canonical, p.removed = False, True
    await session.run_sync(lambda sync: invalidate_coverage(sync, from_block=100, now=NOW))
    await session.commit()
    p.canonical, p.removed = True, False
    p.creation_block_hash = "replacement-creation"
    await session.run_sync(lambda sync: register_pair(sync, p))
    await session.commit()
    resumed = await session.run_sync(lambda sync: claim_work(sync, now=NOW, factories={FACTORY}))
    assert resumed is not None and resumed.creation_hash == "replacement-creation"
    rows = (await session.scalars(select(AlphaSnapshot))).all()
    assert all(r.task_status == "PENDING_INPUT" and r.alpha_score is None for r in rows)


@pytest.mark.asyncio
async def test_publication_and_reconciliation_serialize_across_connections(coverage_session):
    from types import SimpleNamespace

    from smms.config import Settings
    from smms.ingestion.bsc_stream import BSCRealtimeIngestionService

    if coverage_session.bind.dialect.name != "postgresql":
        pytest.skip("independent transactional publication requires PostgreSQL")
    publisher = coverage_session
    item = await claim(publisher)
    scan = await fetch_scan(Provider(empty=True), item, confirmations=1, chunk_size=10)
    assert await persist_scan(publisher, item, scan, now=NOW + timedelta(seconds=1))
    queried = asyncio.Event()

    class Fork:
        async def get_block(self, number, *, include_transactions=False):
            if number == 101:
                queried.set()
            return Block(number=number, block_hash="newfork" if number == 101 else "0xblock100", timestamp=NOW)

    async with async_sessionmaker(publisher.bind, expire_on_commit=False)() as reconciler:
        service = BSCRealtimeIngestionService(SimpleNamespace(settings=Settings(_env_file=None)),
                                            session=reconciler, chain_provider=Fork())
        task = asyncio.create_task(service.reconcile_reorg_window(101, 1))
        try:
            await asyncio.wait_for(queried.wait(), timeout=3)
            await publisher.commit()
            await asyncio.wait_for(task, timeout=5)
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    publisher.expire_all()
    row = await publisher.scalar(select(AlphaCoverageReceipt))
    assert row.canonical is False

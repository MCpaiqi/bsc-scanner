"""Canonical archive commits, audit reads and public health stay truthful."""

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker
from test_alpha_collector import coverage_session as _coverage_session

from smms import runtime
from smms.db.models import WorkerHeartbeat

coverage_session = _coverage_session


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "commit", "cancel"])
async def test_archive_cycle_reports_committed_counts_only(monkeypatch, failure):
    beats = []
    class Work:
        async def run_sync(self, _callback):
            if failure == "cancel":
                raise asyncio.CancelledError
            return {"state": "ARCHIVED", "rows_archived": 512, "segments_written": 1, "bucket_start": 0}
        async def commit(self):
            if failure == "commit":
                raise SQLAlchemyError("private database endpoint")
    @asynccontextmanager
    async def scope(_settings):
        yield Work()
    async def heartbeat(worker, status, **kwargs):
        beats.append((worker, status, deepcopy(kwargs["details"])))
    monkeypatch.setattr(runtime, "session_scope", scope)
    monkeypatch.setattr(runtime, "heartbeat", heartbeat)
    settings = SimpleNamespace(canonical_anchor_hot_blocks=512, bsc_reorg_lookback_blocks=12)
    if failure == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await runtime.run_anchor_archive_cycle(settings)
    else:
        result = await runtime.run_anchor_archive_cycle(settings)
        assert result["committed"] == (failure is None)
    report = beats[-1][2]["research_cycle"]
    assert beats[-1][0] == "anchor-archive" and "private" not in str(beats)
    assert report["rows_archived"] == (512 if failure is None else None)


@pytest.mark.asyncio
async def test_real_archive_cycle_exposes_verified_audit_and_health(coverage_session, monkeypatch):
    from test_canonical_anchor_archive import T0
    from test_canonical_anchor_archive import anchors as seed

    from smms.api import app as api
    from smms.api import research
    from smms.config import Settings

    await coverage_session.run_sync(seed)
    factory = async_sessionmaker(coverage_session.bind, expire_on_commit=False)
    @asynccontextmanager
    async def scope(_settings):
        async with factory() as session:
            yield session
    settings = Settings(_env_file=None, canonical_anchor_hot_blocks=512)
    monkeypatch.setattr(runtime, "session_scope", scope)
    monkeypatch.setattr(runtime, "get_settings", lambda: settings)
    monkeypatch.setattr(research, "session_scope", scope)
    monkeypatch.setattr(research, "get_settings", lambda: settings)
    monkeypatch.setattr(runtime, "_instance_id", lambda: "anchor-archive-fixture")
    report = await runtime.run_anchor_archive_cycle(settings, at=T0)
    assert report["committed"] and report["rows_archived"] == 512
    async with factory() as reader:
        heartbeat = await reader.scalar(select(WorkerHeartbeat))
        assert heartbeat.details_json["last_committed_research_cycle"] == report
        health = await reader.run_sync(lambda sync: api._worker_health(sync, now=datetime.now(UTC)))
        assert health["anchor_archive"]["research"]["cycle"]["rows_archived"] == 512
    listing = await research.anchor_segments(limit=1, before_id=None)
    segment = listing["segments"][0]
    assert "payload" not in segment and segment["row_count"] == 512
    detail = await research.anchor_segment(segment["id"])
    assert detail["verified"] and detail["audit_only"] and len(detail["anchors"]) == 512
    assert detail["anchors"][0]["block_number"] == 0


def test_archive_health_profile_cannot_leak_unknown_or_uncommitted_counts():
    from smms.research.telemetry import research_worker_health

    details = {"research_cycle": {"phase": "ARCHIVE", "committed": False, "rows_archived": 512, "archive_state": "ARCHIVED",
                                 "segments_written": 1, "private_rpc": "private-fixture"}}
    result = research_worker_health(details, worker_type="anchor-archive")
    assert result["cycle"]["rows_archived"] is None and "private" not in str(result)
    assert result["cycle"]["archive_state"] is None


@pytest.mark.asyncio
async def test_real_archive_cancellation_between_copy_and_delete_retains_evidence(coverage_session, monkeypatch):
    from sqlalchemy import event, func
    from test_canonical_anchor_archive import T0
    from test_canonical_anchor_archive import anchors as seed

    from smms.config import Settings
    from smms.db.models import AlphaBlockAnchor, CanonicalAnchorSegment

    if coverage_session.bind.dialect.name != "postgresql":
        pytest.skip("independent asynchronous PostgreSQL cancellation required")
    await coverage_session.run_sync(seed)
    factory = async_sessionmaker(coverage_session.bind, expire_on_commit=False)
    @asynccontextmanager
    async def scope(_settings):
        async with factory() as session:
            yield session
    entered = asyncio.Event()
    def pause(connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("DELETE") and "alpha_block_anchors" in statement:
            entered.set()
            connection.exec_driver_sql("SELECT pg_sleep(5)")
    settings = Settings(_env_file=None, canonical_anchor_hot_blocks=512)
    monkeypatch.setattr(runtime, "session_scope", scope)
    monkeypatch.setattr(runtime, "get_settings", lambda: settings)
    monkeypatch.setattr(runtime, "_instance_id", lambda: "cancel-anchor-archive")
    bind = coverage_session.bind.sync_engine
    event.listen(bind, "before_cursor_execute", pause)
    task = asyncio.create_task(runtime.run_anchor_archive_cycle(settings, at=T0))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        event.remove(bind, "before_cursor_execute", pause)
    async with factory() as reader:
        assert await reader.scalar(select(func.count()).select_from(AlphaBlockAnchor)) == 1536
        assert await reader.scalar(select(func.count()).select_from(CanonicalAnchorSegment)) == 0
        heartbeat = await reader.scalar(select(WorkerHeartbeat))
        assert heartbeat.status == "STOPPED" and heartbeat.details_json["research_cycle"]["rows_archived"] is None
    resumed = await runtime.run_anchor_archive_cycle(settings, at=T0)
    assert resumed["committed"] and resumed["rows_archived"] == 512


@pytest.mark.asyncio
async def test_archive_keeps_the_entire_configured_reconciliation_window(coverage_session, monkeypatch):
    from test_canonical_anchor_archive import T0
    from test_canonical_anchor_archive import anchors as seed

    from smms.config import Settings

    await coverage_session.run_sync(seed)
    factory = async_sessionmaker(coverage_session.bind, expire_on_commit=False)
    @asynccontextmanager
    async def scope(_settings):
        async with factory() as session:
            yield session
    settings = Settings(_env_file=None, canonical_anchor_hot_blocks=512, bsc_reorg_lookback_blocks=1535)
    async def heartbeat(*_args, **_kwargs):
        pass
    monkeypatch.setattr(runtime, "session_scope", scope)
    monkeypatch.setattr(runtime, "heartbeat", heartbeat)
    report = await runtime.run_anchor_archive_cycle(settings, at=T0)
    assert report["committed"] and report["rows_archived"] == 0 and report["archive_state"] == "IDLE"


@pytest.mark.asyncio
async def test_async_archive_busy_state_is_committed_without_false_progress(coverage_session, monkeypatch):
    from test_canonical_anchor_archive import T0
    from test_canonical_anchor_archive import anchors as seed

    from smms.alpha.anchors import anchor_blocks
    from smms.config import Settings

    if coverage_session.bind.dialect.name != "postgresql":
        pytest.skip("independent async PostgreSQL lock owner required")
    await coverage_session.run_sync(seed)
    factory = async_sessionmaker(coverage_session.bind, expire_on_commit=False)
    @asynccontextmanager
    async def scope(_settings):
        async with factory() as session:
            yield session
    settings = Settings(_env_file=None, canonical_anchor_hot_blocks=512)
    monkeypatch.setattr(runtime, "session_scope", scope)
    monkeypatch.setattr(runtime, "get_settings", lambda: settings)
    monkeypatch.setattr(runtime, "_instance_id", lambda: "busy-anchor-archive")
    async with factory() as owner:
        await owner.run_sync(lambda sync: anchor_blocks(sync, {}, observed_at=T0, authoritative=False))
        report = await runtime.run_anchor_archive_cycle(settings, at=T0)
        assert report["committed"] and report["archive_state"] == "BUSY" and report["rows_archived"] == 0
    resumed = await runtime.run_anchor_archive_cycle(settings, at=T0)
    assert resumed["rows_archived"] == 512


@pytest.mark.asyncio
@pytest.mark.parametrize("eligible", [True, False])
async def test_slow_archive_heartbeat_cannot_extend_the_canonical_fence(coverage_session, monkeypatch, eligible):
    from sqlalchemy import text
    from test_canonical_anchor_archive import T0
    from test_canonical_anchor_archive import anchors as seed

    from smms.alpha.anchors import anchor_blocks
    from smms.config import Settings

    if coverage_session.bind.dialect.name != "postgresql":
        pytest.skip("independent canonical publication requires PostgreSQL")
    await coverage_session.run_sync(seed)
    factory = async_sessionmaker(coverage_session.bind, expire_on_commit=False)
    @asynccontextmanager
    async def scope(_settings):
        async with factory() as session:
            yield session
    reporting, release = asyncio.Event(), asyncio.Event()
    async def heartbeat(_worker, _status, **kwargs):
        if kwargs["details"]["research_cycle"]["phase"] != "ARCHIVE":
            reporting.set()
            await release.wait()
    settings = Settings(_env_file=None, canonical_anchor_hot_blocks=512 if eligible else 4096)
    monkeypatch.setattr(runtime, "session_scope", scope)
    monkeypatch.setattr(runtime, "heartbeat", heartbeat)
    task = asyncio.create_task(runtime.run_anchor_archive_cycle(settings, at=T0))
    try:
        await asyncio.wait_for(reporting.wait(), timeout=5)
        async with factory() as publication:
            await publication.execute(text("SET LOCAL lock_timeout='250ms'"))
            # Source publication must not wait for another connection's telemetry.
            await publication.run_sync(lambda sync: anchor_blocks(sync, {}, observed_at=T0, authoritative=False))
    finally:
        release.set()
        result = await task
    assert result["committed"] and result["rows_archived"] == (512 if eligible else 0)


@pytest.mark.asyncio
async def test_corrupt_archive_reconciliation_releases_its_canonical_transaction(coverage_session, monkeypatch):
    from sqlalchemy import text, update
    from test_canonical_anchor_archive import T0
    from test_canonical_anchor_archive import anchors as seed

    from smms.alpha.anchors import anchor_blocks, archive_anchors
    from smms.chains.base import Block
    from smms.config import Settings
    from smms.db.models import CanonicalAnchorSegment
    from smms.ingestion import bsc_stream

    if coverage_session.bind.dialect.name != "postgresql":
        pytest.skip("independent canonical transaction requires PostgreSQL")
    await coverage_session.run_sync(seed)
    await coverage_session.run_sync(lambda sync: archive_anchors(sync, keep_recent_blocks=512, now=T0))
    await coverage_session.commit()
    await coverage_session.execute(update(CanonicalAnchorSegment.__table__).values(payload=b"simulated archive corruption"))
    await coverage_session.commit()
    class Provider:
        async def get_latest_block_number(self):
            return 10
        async def get_block(self, number, **_kwargs):
            return Block(number=number, block_hash="verified-header", timestamp=T0)
    failed = asyncio.Event()
    monkeypatch.setattr(bsc_stream.source_health_registry, "record_failure", lambda *_args: failed.set())
    settings = Settings(_env_file=None)
    service = bsc_stream.BSCRealtimeIngestionService(SimpleNamespace(settings=settings),
        session=coverage_session, chain_provider=Provider())
    task = asyncio.create_task(service._run_reorg_reconciler(settings))
    factory = async_sessionmaker(coverage_session.bind, expire_on_commit=False)
    try:
        await asyncio.wait_for(failed.wait(), timeout=5)
        async with factory() as publication:
            await publication.execute(text("SET LOCAL lock_timeout='250ms'"))
            await publication.run_sync(lambda sync: anchor_blocks(sync, {}, observed_at=T0, authoritative=False))
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await coverage_session.rollback()

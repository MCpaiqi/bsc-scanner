"""Rebuild throughput is durable, whitelisted and truthful about commits."""

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
from smms.research import rebuild

coverage_session = _coverage_session
SETTINGS = SimpleNamespace(runtime_heartbeat_interval_seconds=.005)
COUNTS = {"jobs_examined": 3, "jobs_completed": 1, "jobs_deferred": 1,
          "jobs_unsupported": 1, "jobs_stale": 0, "jobs_superseded": 0}


def fake(monkeypatch, *, failure=None, pause=False):
    beats, state = [], {"committed": False, "rolled_back": False}
    class Work:
        async def run_sync(self, callback):
            if pause:
                await asyncio.sleep(.03)
            return callback(None)
        async def commit(self):
            if failure == "commit":
                raise SQLAlchemyError("private database connection")
            state["committed"] = True
    @asynccontextmanager
    async def scope(_settings):
        try:
            yield Work()
        except BaseException:
            state["rolled_back"] = True
            raise
    async def beat(worker, status, **kwargs):
        beats.append((worker, status, deepcopy(kwargs["details"])))
    def calculate(_session, *, report, **_kwargs):
        report.update(COUNTS, repair_seconds=.05, publication_lock_seconds=.01, rebuild_seconds=.25)
        if failure == "database":
            raise SQLAlchemyError("private database connection")
        if failure == "cancel":
            raise asyncio.CancelledError
        return 3
    monkeypatch.setattr(runtime, "session_scope", scope)
    monkeypatch.setattr(runtime, "heartbeat", beat)
    monkeypatch.setattr(rebuild, "rebuild_due", calculate)
    return beats, state


@pytest.mark.asyncio
async def test_cycle_reports_only_committed_rebuild_counts(monkeypatch):
    beats, state = fake(monkeypatch)
    result = await runtime.run_rebuild_cycle(SETTINGS)
    assert state["committed"] and result["committed"]
    assert all(result[key] == value for key, value in COUNTS.items())
    assert result["publication_lock_seconds"] == .01 and result["commit_seconds"] >= 0
    assert [beat[2]["research_cycle"]["phase"] for beat in beats] == ["REBUILD", "COMMIT", "IDLE"]
    assert all(beat[0] == "research-rebuild" for beat in beats)
    assert beats[-1][2]["last_committed_research_cycle"] == result
    assert all(beat[2]["research_cycle"]["jobs_examined"] is None for beat in beats[:-1])


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["database", "commit", "cancel"])
async def test_rollback_and_cancellation_do_not_report_committed_jobs(monkeypatch, failure):
    beats, state = fake(monkeypatch, failure=failure, pause=True)
    if failure == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await runtime.run_rebuild_cycle(SETTINGS)
    else:
        result = await runtime.run_rebuild_cycle(SETTINGS)
        assert not result["committed"]
    assert state["rolled_back"] and not state["committed"]
    assert beats[-1][1] == ("STOPPED" if failure == "cancel" else "DEGRADED")
    assert all(beat[2]["research_cycle"][key] is None for beat in beats for key in COUNTS)
    assert "private" not in str(beats) and "last_committed_research_cycle" not in beats[-1][2]
    stopped = len(beats)
    await asyncio.sleep(.03)
    assert len(beats) == stopped


@pytest.mark.asyncio
async def test_pulse_updates_elapsed_time_and_stops_before_terminal_report(monkeypatch):
    beats, _state = fake(monkeypatch, pause=True)
    await runtime.run_rebuild_cycle(SETTINGS)
    in_progress = [beat[2]["research_cycle"] for beat in beats if beat[2]["research_cycle"]["phase"] == "REBUILD"]
    assert len(in_progress) > 1 and in_progress[-1]["seconds"] > in_progress[0]["seconds"]
    assert beats[-1][2]["research_cycle"]["phase"] == "IDLE"
    stopped = len(beats)
    await asyncio.sleep(.03)
    assert len(beats) == stopped


@pytest.mark.asyncio
@pytest.mark.parametrize("malformed", [{}, {"phase": []}, {"jobs_examined": True},
    {"repair_seconds": float("inf")}, {"started_at": "0001-01-01T00:00:00+01:00"}])
async def test_health_reads_rebuild_report_from_an_independent_session(coverage_session, monkeypatch, malformed):
    from httpx import ASGITransport, AsyncClient

    from smms.api import app as api
    from smms.config import Settings

    factory = async_sessionmaker(coverage_session.bind, expire_on_commit=False)
    @asynccontextmanager
    async def scope(_settings):
        async with factory() as session:
            yield session
    report = {"phase": "IDLE", "committed": True, "seconds": 1.5, "repair_seconds": .1,
              "publication_lock_seconds": .25, "rebuild_seconds": 1, "commit_seconds": .1,
              "started_at": datetime.now(UTC).isoformat(), **COUNTS, **malformed}
    # PostgreSQL JSON rejects nonfinite JSON numbers. Pure validation covers that
    # shape below; persisted JSON uses a string rather than invalid JSON Infinity.
    if malformed.get("repair_seconds") == float("inf"):
        report["repair_seconds"] = "Infinity"
    async with factory() as writer:
        writer.add(WorkerHeartbeat(instance_id="independent-rebuild", worker_type="research-rebuild", status="RUNNING",
            last_heartbeat_at=datetime.now(UTC), details_json={"research_cycle": report,
                "last_committed_research_cycle": report, "credential": "private-heartbeat-fixture"}))
        await writer.commit()
    async def available(_settings):
        return "ok"
    monkeypatch.setattr(api, "session_scope", scope)
    monkeypatch.setattr(api, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(api, "check_database", available)
    monkeypatch.setattr(api, "check_redis", available)
    async with AsyncClient(transport=ASGITransport(app=api.app), base_url="http://test") as client:
        response = await client.get("/health")
    assert response.status_code == 200 and "private-heartbeat-fixture" not in response.text
    worker = response.json()["workers"]["research_rebuild"]
    assert worker["status"] == "HEALTHY"
    if "phase" in malformed:
        assert worker["research"] is None
    else:
        cycle = worker["research"]["cycle"]
        assert cycle["jobs_completed"] == 1 and cycle["rebuild_seconds"] == 1
        if malformed:
            assert cycle[next(iter(malformed))] is None


def test_rebuild_whitelist_rejects_nonfinite_and_cross_worker_payloads():
    from smms.research.telemetry import research_worker_health

    details = {"research_cycle": {"phase": "REBUILD", "committed": False, "seconds": float("inf"),
                                 "jobs_examined": 4, "jobs_deferred": 4, "rpc_url": "private-fixture"}}
    result = research_worker_health(details, worker_type="research-rebuild")
    assert result["cycle"]["seconds"] is None and result["cycle"]["jobs_deferred"] is None
    assert "private-fixture" not in str(result)
    assert research_worker_health(details) is None


@pytest.mark.asyncio
async def test_real_rebuild_cycle_commits_history_and_durable_timing(coverage_session, monkeypatch):
    from test_rebuild_runtime_budget import NOW, seed_jobs

    from smms.api import app as api
    from smms.config import Settings
    from smms.db.models import ResearchRebuildJob, TokenMarketSnapshot

    originals = await coverage_session.run_sync(seed_jobs)
    factory = async_sessionmaker(coverage_session.bind, expire_on_commit=False)
    @asynccontextmanager
    async def scope(_settings):
        async with factory() as session:
            yield session
    settings = Settings(_env_file=None)
    monkeypatch.setattr(runtime, "session_scope", scope)
    monkeypatch.setattr(runtime, "get_settings", lambda: settings)
    monkeypatch.setattr(runtime, "_instance_id", lambda: "real-rebuild-fixture")
    report = await runtime.run_rebuild_cycle(settings, at=NOW)
    assert report["committed"] and report["jobs_examined"] == report["jobs_completed"] == 2
    async with factory() as reader:
        assert set(await reader.scalars(select(ResearchRebuildJob.status))) == {"COMPLETE"}
        history = (await reader.scalars(select(TokenMarketSnapshot)
                                       .execution_options(include_invalid_snapshots=True))).all()
        assert len(history) == 4 and all(original.price_usd == 7 for original in originals)
        worker = await reader.scalar(select(WorkerHeartbeat))
        assert worker.details_json["last_committed_research_cycle"] == report
        health = await reader.run_sync(lambda sync: api._worker_health(sync, now=datetime.now(UTC)))
        cycle = health["research_rebuild"]["research"]["cycle"]
        assert cycle["jobs_completed"] == 2 and cycle["publication_lock_seconds"] >= 0


@pytest.mark.asyncio
async def test_failed_heartbeat_does_not_erase_rebuild_commit(monkeypatch):
    _beats, state = fake(monkeypatch)
    async def unavailable(*_args, **_kwargs):
        raise OSError("private heartbeat endpoint")
    monkeypatch.setattr(runtime, "heartbeat", unavailable)
    report = await runtime.run_rebuild_cycle(SETTINGS)
    assert state["committed"] and report["committed"] and report["jobs_completed"] == 1


@pytest.mark.asyncio
async def test_real_cancellation_releases_rebuild_and_publication_locks(coverage_session, monkeypatch):
    from sqlalchemy import text
    from test_rebuild_runtime_budget import NOW, seed_jobs

    from smms.config import Settings
    from smms.db.models import ResearchRebuildJob

    if coverage_session.bind.dialect.name != "postgresql":
        pytest.skip("cancellation of a live locked database transaction requires PostgreSQL")
    await coverage_session.run_sync(seed_jobs)
    factory = async_sessionmaker(coverage_session.bind, expire_on_commit=False)
    @asynccontextmanager
    async def scope(_settings):
        async with factory() as session:
            yield session
    entered, original = asyncio.Event(), rebuild._rebuild
    def pause(session, old):
        from smms.research.publication import IDENTITY, lock_identity

        # Exercise cancellation while actual publication and job locks are held;
        # the earlier preflight probe has already released its temporary lock.
        lock_identity(session, type(old), {name: getattr(old, name) for name in IDENTITY[type(old)]})
        entered.set()
        session.execute(text("SELECT pg_sleep(5)"))
    monkeypatch.setattr(rebuild, "_rebuild", pause)
    settings = Settings(_env_file=None)
    monkeypatch.setattr(runtime, "session_scope", scope)
    monkeypatch.setattr(runtime, "get_settings", lambda: settings)
    monkeypatch.setattr(runtime, "_instance_id", lambda: "cancel-rebuild-fixture")
    task = asyncio.create_task(runtime.run_rebuild_cycle(settings, at=NOW))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    async with factory() as reader:
        jobs = (await reader.scalars(select(ResearchRebuildJob))).all()
        assert all(job.status == "PENDING" and job.attempts == 0 for job in jobs)
        worker = await reader.scalar(select(WorkerHeartbeat))
        assert worker.status == "STOPPED" and worker.details_json["research_cycle"]["jobs_completed"] is None
    monkeypatch.setattr(rebuild, "_rebuild", original)
    resumed = await runtime.run_rebuild_cycle(settings, at=NOW)
    assert resumed["committed"] and resumed["jobs_completed"] == 2

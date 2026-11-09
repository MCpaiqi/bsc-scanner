"""Phase latency and committed outcomes survive a worker/API process boundary."""

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
from smms.alpha import live
from smms.db.models import WorkerHeartbeat

coverage_session = _coverage_session


def fake_cycle(monkeypatch, *, failure=None):
    beats, state = [], {"committed": False, "rolled_back": False}
    class Work:
        async def run_sync(self, callback):
            return callback(None)
        async def commit(self):
            if failure == "commit":
                raise SQLAlchemyError("private error detail")
            state["committed"] = True
    @asynccontextmanager
    async def scope(_settings):
        try:
            yield Work()
        except BaseException:
            state["rolled_back"] = True
            raise
    async def heartbeat(worker, status, **kwargs):
        beats.append((worker, status, deepcopy(kwargs.get("details", {}))))
    def alpha(_session, **_kwargs):
        if failure == "alpha":
            raise OSError("private provider detail")
        if failure == "cancel":
            raise asyncio.CancelledError
        return 4
    monkeypatch.setattr(runtime, "session_scope", scope)
    monkeypatch.setattr(runtime, "heartbeat", heartbeat)
    monkeypatch.setattr(runtime, "evaluate_due", lambda _session, **_kwargs: 2)
    monkeypatch.setattr(live, "process_live_alpha", alpha)
    return beats, state


@pytest.mark.asyncio
async def test_forward_cycle_reports_stage_latencies_and_only_committed_counts(monkeypatch):
    beats, state = fake_cycle(monkeypatch)
    result = await runtime.run_forward_cycle(SimpleNamespace(alpha_min_future_quote_notional=.01))
    assert state["committed"] and result["committed"]
    assert result["forward_evaluated"] == 2 and result["alpha_scored"] == 4
    assert [beat[2]["research_cycle"]["phase"] for beat in beats] == ["FORWARD", "ALPHA", "COMMIT", "IDLE"]
    assert all(result[key] >= 0 for key in ["seconds", "forward_seconds", "alpha_seconds", "commit_seconds"])
    assert beats[-1][2]["last_committed_research_cycle"] == result


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["alpha", "commit"])
async def test_failed_cycle_reports_degradation_after_rollback_without_committed_counts(monkeypatch, failure):
    beats, state = fake_cycle(monkeypatch, failure=failure)
    result = await runtime.run_forward_cycle(SimpleNamespace(alpha_min_future_quote_notional=.01))
    assert state["rolled_back"] and not state["committed"] and not result["committed"]
    assert result["forward_evaluated"] is None and result["alpha_scored"] is None
    assert beats[-1][1] == "DEGRADED"
    assert "last_committed_research_cycle" not in beats[-1][2]
    assert "private" not in str(beats[-1][2])


@pytest.mark.asyncio
async def test_cancelled_cycle_rolls_back_and_propagates_cancellation(monkeypatch):
    beats, state = fake_cycle(monkeypatch, failure="cancel")
    with pytest.raises(asyncio.CancelledError):
        await runtime.run_forward_cycle(SimpleNamespace(alpha_min_future_quote_notional=.01))
    assert state["rolled_back"] and not state["committed"]
    assert beats[-1][1] == "STOPPED" and not beats[-1][2]["research_cycle"]["committed"]


@pytest.mark.asyncio
async def test_first_heartbeat_retains_its_phase_details(coverage_session, monkeypatch):
    factory = async_sessionmaker(coverage_session.bind, expire_on_commit=False)
    @asynccontextmanager
    async def scope(_settings):
        async with factory() as session:
            yield session
    monkeypatch.setattr(runtime, "session_scope", scope)
    monkeypatch.setattr(runtime, "get_settings", lambda: None)
    monkeypatch.setattr(runtime, "_instance_id", lambda: "runtime-budget-fixture")
    await runtime.heartbeat("forward-worker", "RUNNING", details={"research_cycle": {"phase": "FORWARD", "committed": False}})
    async with factory() as reader:
        row = await reader.scalar(select(WorkerHeartbeat))
        assert row.details_json["research_cycle"]["phase"] == "FORWARD"


@pytest.mark.asyncio
async def test_health_reads_committed_research_timing_from_an_independent_session(coverage_session, monkeypatch):
    from httpx import ASGITransport, AsyncClient

    from smms.api import app as api
    from smms.config import Settings

    factory = async_sessionmaker(coverage_session.bind, expire_on_commit=False)
    @asynccontextmanager
    async def scope(_settings):
        async with factory() as session:
            yield session
    report = {"phase": "IDLE", "committed": True, "seconds": 1.25, "forward_seconds": .5,
              "alpha_seconds": .5, "commit_seconds": .1, "forward_evaluated": 2, "alpha_scored": 4,
              "started_at": datetime.now(UTC).isoformat(), "finished_at": datetime.now(UTC).isoformat(),
              "rpc_url": "untrusted-heartbeat-payload-secret"}
    async with factory() as writer:
        writer.add(WorkerHeartbeat(instance_id="separate-process", worker_type="forward-worker", status="RUNNING",
            last_heartbeat_at=datetime.now(UTC), details_json={"research_cycle": report, "last_committed_research_cycle": report,
                "credential": "untrusted-heartbeat-payload-secret"}))
        await writer.commit()
    async def available(_settings):
        return "ok"
    monkeypatch.setattr(api, "session_scope", scope)
    monkeypatch.setattr(api, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(api, "check_database", available)
    monkeypatch.setattr(api, "check_redis", available)
    async with AsyncClient(transport=ASGITransport(app=api.app), base_url="http://test") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    research = response.json()["workers"]["forward_worker"]["research"]
    assert research["cycle"]["seconds"] == 1.25
    assert research["last_committed_cycle"]["alpha_scored"] == 4
    assert "untrusted-heartbeat-payload-secret" not in response.text


def test_health_rejects_unknown_cycle_fields_and_uncommitted_counters():
    from smms.research.telemetry import research_worker_health

    result = research_worker_health({"research_cycle": {"phase": "ALPHA", "committed": False,
        "seconds": -1, "alpha_seconds": "NaN", "forward_seconds": True,
        "forward_evaluated": 12, "alpha_scored": 7, "started_at": "private-url",
        "error_code": "untrusted-heartbeat-payload-secret"},
        "last_committed_research_cycle": {"phase": "IDLE", "committed": False}})
    assert result["cycle"]["phase"] == "ALPHA"
    assert all(result["cycle"][key] is None for key in ["seconds", "alpha_seconds", "forward_seconds",
        "forward_evaluated", "alpha_scored", "started_at", "error_code"])
    assert result["last_committed_cycle"] is None
    assert research_worker_health({"research_cycle": {"phase": "untrusted", "committed": True}}) is None


@pytest.mark.asyncio
async def test_transient_report_failure_does_not_erase_a_successful_commit(monkeypatch):
    _beats, state = fake_cycle(monkeypatch)
    async def unavailable(*_args, **_kwargs):
        raise OSError("private endpoint detail")
    monkeypatch.setattr(runtime, "heartbeat", unavailable)
    report = await runtime.run_forward_cycle(SimpleNamespace(alpha_min_future_quote_notional=.01))
    assert state["committed"] and report["committed"] and report["alpha_scored"] == 4


@pytest.mark.asyncio
async def test_real_cycle_commits_alpha_and_exposes_its_report_to_another_session(coverage_session, monkeypatch):
    from datetime import timedelta

    from test_alpha_coverage_integrity import T0, receipt, trade

    from smms.config import Settings
    from smms.db.models import AlphaSnapshot, Pair

    def seed(sync):
        receipt(sync, sync.scalar(select(Pair)))
        trade(sync, 10)
    await coverage_session.run_sync(seed)
    await coverage_session.commit()
    factory = async_sessionmaker(coverage_session.bind, expire_on_commit=False)
    @asynccontextmanager
    async def scope(_settings):
        async with factory() as session:
            yield session
    settings = Settings(_env_file=None)
    monkeypatch.setattr(runtime, "session_scope", scope)
    monkeypatch.setattr(runtime, "get_settings", lambda: settings)
    monkeypatch.setattr(runtime, "_instance_id", lambda: "real-cycle-fixture")
    cutoff = T0 + timedelta(seconds=4000)
    report = await runtime.run_forward_cycle(settings, at=cutoff)
    assert report["committed"] and report["alpha_scored"] == 4 and report["forward_evaluated"] == 0
    async with factory() as reader:
        snapshots = (await reader.scalars(select(AlphaSnapshot))).all()
        worker = await reader.scalar(select(WorkerHeartbeat))
        assert len(snapshots) == 4 and all(row.task_status == "COMPLETE" for row in snapshots)
        persisted = worker.details_json["last_committed_research_cycle"]
        assert persisted["alpha_scored"] == 4 and persisted["evaluation_as_of"] == cutoff.isoformat()


@pytest.mark.asyncio
@pytest.mark.parametrize("malformed", [{"phase": []}, {"error_code": {}}, {"started_at": "0001-01-01T00:00:00+01:00"}])
async def test_health_endpoint_survives_malformed_cycle_fields(coverage_session, monkeypatch, malformed):
    from httpx import ASGITransport, AsyncClient

    from smms.api import app as api
    from smms.config import Settings

    factory = async_sessionmaker(coverage_session.bind, expire_on_commit=False)
    @asynccontextmanager
    async def scope(_settings):
        async with factory() as session:
            yield session
    report = {"phase": "IDLE", "committed": True, "seconds": 1, "error_code": None,
              "forward_evaluated": 2, "alpha_scored": 4, **malformed}
    async with factory() as writer:
        writer.add(WorkerHeartbeat(instance_id="malformed-fixture", worker_type="forward-worker", status="RUNNING",
            last_heartbeat_at=datetime.now(UTC), details_json={"research_cycle": report}))
        await writer.commit()
    async def available(_settings):
        return "ok"
    monkeypatch.setattr(api, "session_scope", scope)
    monkeypatch.setattr(api, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(api, "check_database", available)
    monkeypatch.setattr(api, "check_redis", available)
    async with AsyncClient(transport=ASGITransport(app=api.app), base_url="http://test") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    research = response.json()["workers"]["forward_worker"]["research"]
    if "phase" in malformed:
        assert research is None
    else:
        key = next(iter(malformed))
        assert research["cycle"][key] is None
        assert research["cycle"]["alpha_scored"] == 4

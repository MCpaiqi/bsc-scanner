"""Reconstruction contention yields instead of stalling independent due jobs."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session
from test_research_integrity import research_session as _research_session

from smms.db.models import (
    ResearchRebuildCursor,
    ResearchRebuildJob,
    ResearchSnapshotLock,
    TokenMarketSnapshot,
)
from smms.evaluation import utc
from smms.research import rebuild
from smms.research.lineage import manifest
from smms.research.publication import IDENTITY, MODELS, lock_identity, publish_snapshot

research_session = _research_session
T0 = datetime(2026, 1, 1, tzinfo=UTC)
NOW = T0 + timedelta(days=2)


def seed_jobs(session, count=2):
    rows = []
    for number in range(count):
        row = publish_snapshot(session, TokenMarketSnapshot(
            chain="bsc", chain_id=56, token_address=f"token-{number}", pair_address=f"pair-{number}",
            source="ON_CHAIN_TRADE_AGGREGATION", observed_at=T0, as_of=T0, source_epoch=0,
            snapshot_status="INVALIDATED", price_usd=Decimal(7),
            input_manifest=manifest(kind="token_market_snapshots", parameters={}, facts={"original": number}),
        ))
        rows.append(row)
        session.add(ResearchRebuildJob(kind=row.__tablename__, snapshot_id=row.id, requested_epoch=0,
                                      status="PENDING", attempts=0, next_attempt_at=NOW))
    session.commit()
    return rows


def postgres(session):
    if session.get_bind().dialect.name != "postgresql":
        pytest.skip("independent database locks require PostgreSQL")


@pytest.mark.parametrize("cold", [False, True])
def test_repair_owner_cannot_block_an_independent_due_batch(research_session, cold):
    postgres(research_session)
    seed_jobs(research_session)
    if not cold:
        rebuild._repair(research_session, epoch=0, now=NOW, batch_size=2)
        research_session.commit()
    with Session(research_session.get_bind()) as owner, Session(research_session.get_bind()) as other:
        if cold:
            rebuild._repair(owner, epoch=0, now=NOW, batch_size=2)
        else:
            owner.scalar(select(ResearchRebuildCursor).where(
                ResearchRebuildCursor.kind == next(iter(MODELS)),
            ).with_for_update())
        other.execute(text("SET LOCAL lock_timeout='250ms'"))
        other.execute(text("SET LOCAL statement_timeout='2s'"))
        assert rebuild.rebuild_due(other, now=NOW) == 2
        other.commit()
        assert set(other.scalars(select(ResearchRebuildJob.status))) == {"COMPLETE"}


@pytest.mark.parametrize("cold", [False, True])
def test_busy_root_defers_before_reading_facts_and_other_job_commits(research_session, monkeypatch, cold):
    postgres(research_session)
    rows = seed_jobs(research_session)
    identity = {name: getattr(rows[0], name) for name in IDENTITY[TokenMarketSnapshot]}
    if cold:
        key = lock_identity(research_session, TokenMarketSnapshot, identity)
        research_session.execute(delete(ResearchSnapshotLock).where(ResearchSnapshotLock.identity == key))
        research_session.commit()
    attempted, original = [], rebuild._rebuild
    def observe(session, old):
        attempted.append(old.id)
        return original(session, old)
    monkeypatch.setattr(rebuild, "_rebuild", observe)
    with Session(research_session.get_bind()) as owner, Session(research_session.get_bind()) as other:
        lock_identity(owner, TokenMarketSnapshot, identity)
        other.execute(text("SET LOCAL statement_timeout='2s'"))
        assert rebuild.rebuild_due(other, now=NOW) == 2
        other.commit()
        jobs = other.scalars(select(ResearchRebuildJob).order_by(ResearchRebuildJob.id)).all()
        assert [job.status for job in jobs] == ["PENDING", "COMPLETE"]
        assert utc(jobs[0].next_attempt_at) == NOW + timedelta(seconds=60)
        assert jobs[0].attempts == 1 and jobs[0].last_error
        assert attempted == [rows[1].id]
        assert other.scalar(text("SHOW lock_timeout")) == "0"
    with Session(research_session.get_bind()) as restarted:
        assert rebuild.rebuild_due(restarted, now=NOW + timedelta(seconds=61)) >= 1
        restarted.commit()
        assert set(restarted.scalars(select(ResearchRebuildJob.status))) == {"COMPLETE"}


@pytest.mark.parametrize("original", [0, 75, 1000])
@pytest.mark.parametrize("fail", [False, True])
def test_lock_deadline_preserves_shorter_setting_and_restores_after_savepoint(research_session, original, fail):
    postgres(research_session)
    from smms.research.maintenance import bounded_lock_wait

    research_session.execute(text("SELECT set_config('lock_timeout', :value, true)"), {"value": f"{original}ms"})
    def setting():
        return int(research_session.scalar(text("SELECT setting FROM pg_settings WHERE name='lock_timeout'")))
    try:
        with research_session.begin_nested(), bounded_lock_wait(research_session, milliseconds=250):
            assert setting() == min(original or 250, 250)
            if fail:
                raise ValueError("synthetic failure")
    except ValueError:
        assert fail
    assert setting() == original


def test_batch_report_preserves_unknown_history_and_due_job_boundary(research_session):
    originals = seed_jobs(research_session, count=3)
    future = research_session.scalar(select(ResearchRebuildJob).order_by(ResearchRebuildJob.id.desc()))
    future.next_attempt_at = NOW + timedelta(days=1)
    report = {}
    assert rebuild.rebuild_due(research_session, now=NOW, batch_size=2, report=report) == 2
    research_session.commit()
    assert report["jobs_examined"] == report["jobs_completed"] == 2
    assert report["jobs_deferred"] == report["jobs_unsupported"] == report["jobs_stale"] == report["jobs_superseded"] == 0
    assert all(report[key] >= 0 for key in ("repair_seconds", "publication_lock_seconds", "rebuild_seconds"))
    for original in originals[:2]:
        current = research_session.scalar(select(TokenMarketSnapshot).where(
            TokenMarketSnapshot.previous_snapshot_id == original.id,
        ).execution_options(include_invalid_snapshots=True))
        assert current.snapshot_status == "UNAVAILABLE" and current.price_usd is None
        assert current.input_manifest["facts"]["trades"] == []
        assert original.price_usd == 7 and "original" in original.input_manifest["facts"]
    assert future.status == "PENDING" and future.attempts == 0


def test_dependency_lock_timeout_rolls_back_only_its_job(research_session, monkeypatch):
    postgres(research_session)
    rows = seed_jobs(research_session)
    dependency = publish_snapshot(research_session, TokenMarketSnapshot(
        chain="bsc", token_address="dependency", pair_address="dependency-pair", as_of=T0,
        observed_at=T0, source="DEPENDENCY_FIXTURE", source_epoch=0,
        input_manifest=manifest(kind="token_market_snapshots", parameters={}, facts={"canonical": True}),
    ))
    research_session.commit()
    original = rebuild._rebuild
    def calculate(session, old):
        if old.id != rows[0].id:
            return original(session, old)
        publish_snapshot(session, TokenMarketSnapshot(
            **{name: getattr(old, name) for name in IDENTITY[TokenMarketSnapshot]},
            chain_id=56, observed_at=T0, source_epoch=0,
            input_manifest=manifest(kind="token_market_snapshots", parameters={}, facts={}, dependencies=[{
                "kind": dependency.__tablename__, "id": dependency.id, "digest": dependency.input_manifest["digest"],
            }]),
        ))
    monkeypatch.setattr(rebuild, "_rebuild", calculate)
    with Session(research_session.get_bind()) as owner, Session(research_session.get_bind()) as other:
        owner.scalar(select(TokenMarketSnapshot).where(TokenMarketSnapshot.id == dependency.id).with_for_update())
        other.execute(text("SET LOCAL statement_timeout='2s'"))
        errors = []
        from sqlalchemy import event

        def observe_error(context):
            errors.append(getattr(context.original_exception, "pgcode", None))
        event.listen(other.get_bind(), "handle_error", observe_error)
        try:
            assert rebuild.rebuild_due(other, now=NOW) == 2
            other.commit()
        finally:
            event.remove(other.get_bind(), "handle_error", observe_error)
        assert errors == ["55P03"]  # lock_timeout, rather than statement timeout57014
        assert list(other.scalars(select(ResearchRebuildJob.status).order_by(ResearchRebuildJob.id))) == ["PENDING", "COMPLETE"]
        assert other.scalar(select(TokenMarketSnapshot.id).where(TokenMarketSnapshot.previous_snapshot_id == rows[0].id)
                            .execution_options(include_invalid_snapshots=True)) is None
        assert other.scalar(text("SHOW lock_timeout")) == "0"


def test_rebuild_read_does_not_hold_up_a_fresh_live_publication(research_session, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    postgres(research_session)
    rows = seed_jobs(research_session)
    reading, released, original = Event(), Event(), rebuild._rebuild
    def pause(session, old):
        if old.id == rows[0].id:
            reading.set()
            assert released.wait(timeout=5)
        return original(session, old)
    monkeypatch.setattr(rebuild, "_rebuild", pause)
    def worker():
        with Session(research_session.get_bind()) as session:
            assert rebuild.rebuild_due(session, now=NOW) == 2
            session.commit()
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(worker)
        try:
            assert reading.wait(timeout=5)
            with Session(research_session.get_bind()) as live:
                live.execute(text("SET LOCAL lock_timeout='250ms'"))
                fresh = publish_snapshot(live, TokenMarketSnapshot(
                    **{name: getattr(rows[0], name) for name in IDENTITY[TokenMarketSnapshot]},
                    chain_id=56, observed_at=T0, source_epoch=0, price_usd=9,
                    input_manifest=manifest(kind="token_market_snapshots", parameters={"policy": "fresh"}, facts={}),
                ))
                live.commit()
                fresh_id = fresh.id
        finally:
            released.set()
        future.result(timeout=5)
    current = research_session.scalar(select(TokenMarketSnapshot).where(TokenMarketSnapshot.token_address == rows[0].token_address))
    assert current.id == fresh_id and current.price_usd == 9
    assert current.input_manifest["parameters"]["policy"] == "fresh"

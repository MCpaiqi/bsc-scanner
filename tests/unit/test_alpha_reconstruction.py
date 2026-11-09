"""Append-only canonical Alpha reconstruction and exact source lineage."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from threading import Barrier

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session
from test_alpha_collector import coverage_session as _coverage_session
from test_alpha_coverage_integrity import PAIR, T0, pair, receipt, trade
from test_research_integrity import research_session as _research_session

from smms.alpha.live import process_live_alpha
from smms.alpha.work import invalidate_coverage, register_pair
from smms.api.radar import _alpha_payload, _fresh_alpha_latest_rows, alpha_statistics
from smms.db.models import AlphaCoverageJob, AlphaSnapshot, Pair
from smms.evaluation import utc

research_session = _research_session
coverage_session = _coverage_session
NOW = T0 + timedelta(days=2)


def seed_scored(session):
    p = pair(session)
    entry = trade(session, 10)
    receipt(session, p)
    process_live_alpha(session, now=NOW)
    session.flush()
    return p, entry


def test_reorg_enqueues_replacements_without_overwriting_scored_inputs(research_session):
    p, entry = seed_scored(research_session)
    original = research_session.scalars(select(AlphaSnapshot).order_by(AlphaSnapshot.id)).all()
    frozen = deepcopy([(r.alpha_score, r.entry_vwap, r.features_json, r.components_json) for r in original])
    entry.canonical, entry.removed = False, True
    invalidate_coverage(research_session, from_block=110, now=NOW)
    fresh_entry = trade(research_session, 10, price="4", tx="replacement-entry")
    new_receipt = receipt(research_session, p)
    new_receipt.completed_at = NOW + timedelta(seconds=1)

    process_live_alpha(research_session, now=NOW + timedelta(seconds=2))

    rows = research_session.scalars(select(AlphaSnapshot).order_by(AlphaSnapshot.id)).all()
    assert len(rows) == 8
    assert all(r.task_status == "INVALIDATED" for r in original)
    assert all(r.outcome_json["invalidated_outcome"]["reached_10x"] is False for r in original)
    assert frozen == [(r.alpha_score, r.entry_vwap, r.features_json, r.components_json) for r in original]
    rebuilt = rows[4:]
    assert all(r.task_status == "COMPLETE" and r.entry_vwap == 4 for r in rebuilt)
    assert {r.supersedes_id for r in rebuilt} == {r.id for r in original}
    assert all(r.input_revision == 1 and r.coverage_generation == 1 for r in rebuilt)
    assert all(r.input_manifest["trade_ids"] == [fresh_entry.id] for r in rebuilt)
    visible = _fresh_alpha_latest_rows(research_session, now=NOW + timedelta(seconds=2),
                                       fresh_seconds=200000, limit=100)
    assert len(visible) == 1 and visible[0].input_revision == 1
    stats = alpha_statistics(research_session, now=NOW + timedelta(seconds=2), minimum_quote=Decimal("0.01"))
    assert stats["matured_1h"] == 1


def test_rebuild_waits_for_new_generation_coverage(research_session):
    seed_scored(research_session)
    invalidate_coverage(research_session, from_block=200, now=NOW)
    process_live_alpha(research_session, now=NOW + timedelta(days=3))
    rows = research_session.scalars(select(AlphaSnapshot)).all()
    assert len(rows) == 8
    pending = [r for r in rows if r.task_status == "PENDING_INPUT"]
    assert len(pending) == 4 and all(r.alpha_score is None for r in pending)
    assert all(r.outcome_json["reached_10x"] is None for r in pending)
    assert all(r.supersedes_id is not None for r in pending)


def test_input_manifest_is_frozen_and_future_inputs_are_separate(research_session):
    p = pair(research_session)
    entry = trade(research_session, 10)
    receipt(research_session, p, end_seconds=30)
    process_live_alpha(research_session, now=T0 + timedelta(seconds=30))
    row = research_session.scalar(select(AlphaSnapshot).where(AlphaSnapshot.observation_seconds == 30))
    manifest = deepcopy(row.input_manifest)
    assert manifest["trade_ids"] == [entry.id]
    future = trade(research_session, 40, price="6")
    process_live_alpha(research_session, now=T0 + timedelta(seconds=100))
    assert row.input_manifest == manifest
    evaluation = row.outcome_json["evaluation_manifest"]
    assert evaluation["trade_ids"] == [future.id]
    assert evaluation["entry_manifest_digest"] == manifest["digest"]
    assert row.outcome_json["reached_3x"] is True


def test_registration_converges_on_one_replacement_per_window(research_session):
    p, _ = seed_scored(research_session)
    invalidate_coverage(research_session, from_block=200, now=NOW)
    for _ in range(3):
        register_pair(research_session, p)
    rows = research_session.scalars(select(AlphaSnapshot)).all()
    assert len(rows) == 8
    assert len([r for r in rows if r.task_status == "PENDING_INPUT"]) == 4


def test_rediscovery_keeps_old_creation_time_and_makes_new_windows(research_session):
    p, _ = seed_scored(research_session)
    original = research_session.scalars(select(AlphaSnapshot)).all()
    p.canonical, p.removed = False, True
    invalidate_coverage(research_session, from_block=100, now=NOW)
    p.canonical, p.removed = True, False
    p.creation_timestamp = T0 + timedelta(seconds=5)
    p.creation_block, p.creation_block_hash = 105, "replacement-creation"
    register_pair(research_session, p)
    research_session.flush()
    rows = research_session.scalars(select(AlphaSnapshot).where(AlphaSnapshot.task_status == "PENDING_INPUT")).all()
    assert len(rows) == 4
    assert all(utc(r.pair_created_at) == p.creation_timestamp for r in rows)
    assert all(utc(r.pair_created_at) == T0 for r in original)


def test_never_scanned_pair_rediscovery_resets_pending_anchor(research_session):
    p = pair(research_session)
    register_pair(research_session, p)
    original = research_session.scalars(select(AlphaSnapshot)).all()
    p.canonical, p.removed = False, True
    invalidate_coverage(research_session, from_block=100, now=NOW)
    p.canonical, p.removed = True, False
    p.creation_timestamp, p.creation_block = T0 + timedelta(seconds=5), 105
    p.creation_block_hash = "replacement"
    register_pair(research_session, p)
    research_session.flush()
    job = research_session.scalar(select(AlphaCoverageJob))
    assert job.next_block == 105 and job.generation > 0
    pending = research_session.scalars(select(AlphaSnapshot).where(AlphaSnapshot.task_status == "PENDING_INPUT")).all()
    assert len(pending) == 4 and all(row.input_revision == 1 for row in pending)
    assert all(utc(row.pair_created_at) == p.creation_timestamp for row in pending)
    assert all(row.task_status == "INVALIDATED" for row in original)


def test_creation_block_enrichment_recovers_previously_unscannable_job(research_session):
    p = pair(research_session)
    p.creation_block = None
    register_pair(research_session, p)
    p.creation_block = 100
    register_pair(research_session, p)
    assert research_session.scalar(select(AlphaCoverageJob)).next_block == 100


def test_manifest_digest_reproduces_after_a_database_reload(research_session):
    from smms.alpha.lineage import digest_manifest

    seed_scored(research_session)
    research_session.commit()
    research_session.expire_all()
    for row in research_session.scalars(select(AlphaSnapshot)):
        assert digest_manifest(row.input_manifest) == row.input_manifest["digest"]
        evaluation = row.outcome_json["evaluation_manifest"]
        assert digest_manifest(evaluation) == evaluation["digest"]


def test_manifest_digest_keeps_all_38_decimal_digits():
    from smms.alpha.lineage import digest_manifest

    first = Decimal("12345678901234567890.123456789012345678")
    second = Decimal("12345678901234567890.123456789012345679")
    assert digest_manifest({"quote": first}) != digest_manifest({"quote": second})


def test_live_payload_keeps_full_source_values_on_the_lineage_endpoint(research_session):
    seed_scored(research_session)
    row = research_session.scalar(select(AlphaSnapshot))
    payload = _alpha_payload(row, now=NOW)
    assert "trades" not in payload["input_manifest"]
    assert "trades" not in payload["outcome"]["evaluation_manifest"]
    assert payload["input_manifest"]["trade_count"] == 1
    assert payload["lineage_url"].endswith(f"/{row.id}/lineage")


def test_concurrent_registration_creates_only_one_replacement(research_session):
    if research_session.bind.dialect.name != "postgresql":
        pytest.skip("two independent writers require PostgreSQL")
    p, _ = seed_scored(research_session)
    invalidate_coverage(research_session, from_block=200, now=NOW)
    research_session.execute(delete(AlphaSnapshot).where(AlphaSnapshot.alpha_score.is_(None)))
    research_session.commit()
    ready = Barrier(2)
    pair_id = p.id

    def writer():
        with Session(research_session.bind) as session:
            session.execute(text("SET LOCAL lock_timeout = '5s'"))
            current_pair = session.get(Pair, pair_id)
            ready.wait(timeout=3)
            register_pair(session, current_pair)
            session.commit()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(writer) for _ in range(2)]
        for future in futures:
            future.result(timeout=10)
    rows = research_session.scalars(select(AlphaSnapshot)).all()
    assert len(rows) == 8
    assert len([row for row in rows if row.task_status == "PENDING_INPUT"]) == 4


@pytest.mark.asyncio
async def test_revision_history_is_bounded_and_current_detail_shows_rebuilding(coverage_session, monkeypatch):
    from smms.api import radar

    session = coverage_session

    def prepare(sync):
        p = sync.scalar(select(Pair))
        trade(sync, 10)
        receipt(sync, p)
        process_live_alpha(sync, now=NOW)
        invalidate_coverage(sync, from_block=200, now=NOW)

    await session.run_sync(prepare)
    await session.commit()

    @asynccontextmanager
    async def scope(_settings):
        yield session

    monkeypatch.setattr(radar, "session_scope", scope)
    ids, cursor = [], None
    for _ in range(3):
        page = await radar.alpha_revisions(PAIR, limit=3, before_id=cursor)
        assert page["audit_only"] is True and len(page["revisions"]) <= 3
        ids.extend(row["input_revision"] for row in page["revisions"])
        cursor = page["next_cursor"]
    assert sorted(ids) == [0] * 4 + [1] * 4 and cursor is None
    current = await radar.alpha_candidate(PAIR)
    assert current["latest"]["task_status"] == "PENDING_INPUT"
    assert current["latest"]["alpha_score"] is None
    archived = await session.scalar(select(AlphaSnapshot).where(AlphaSnapshot.input_revision == 0))
    lineage = await radar.alpha_lineage(archived.id)
    assert lineage["audit_only"] is True and lineage["input_manifest"]["trades"]

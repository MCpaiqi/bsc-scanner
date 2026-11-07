"""Canonical projection effects commit atomically and survive relay interruption."""

from datetime import timedelta

import pytest
from sqlalchemy import select
from test_derived_snapshot_integrity import PAIR, T0, TOKEN, fact, seed
from test_research_integrity import research_session as _research_session

from smms.db.models import (
    AlertHistory,
    AlertRule,
    LiveSignalObservation,
    Pair,
    RadarAuditJob,
    RadarCandidateEvidence,
    TokenMarketSnapshot,
)
from smms.radar.hooks import project, refresh_deferred_rows

research_session = _research_session
AT = T0 + timedelta(seconds=20)


class AsyncShim:
    def __init__(self, session):
        self.session = session

    async def run_sync(self, callback):
        return callback(self.session)


@pytest.mark.asyncio
async def test_uncommitted_projection_never_publishes(research_session, monkeypatch):
    seed(research_session)
    pair = research_session.scalar(select(Pair).where(Pair.address == PAIR))
    published = []

    async def publish(*args, **kwargs):
        published.append((args, kwargs))

    monkeypatch.setattr("smms.radar.events.publish_domain_event", publish)
    await project(AsyncShim(research_session), pair)
    assert published == []
    research_session.rollback()
    from smms.db.models import RadarEventOutbox

    assert research_session.scalars(select(RadarEventOutbox)).all() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["normal", "deferred"])
async def test_normal_and_deferred_retain_evidence_and_all_final_effects(research_session, monkeypatch, mode):
    from smms.radar import live_forward

    monkeypatch.setattr(live_forward, "_sample_rejected", lambda *_args, **_kwargs: True)
    seed(research_session)
    pair = research_session.scalar(select(Pair).where(Pair.address == PAIR))
    first = fact(research_session)
    second = fact(research_session, tx="two", at=T0 + timedelta(seconds=15))
    research_session.add(TokenMarketSnapshot(chain="bsc", token_address=TOKEN, pair_address=PAIR,
                                             source="DEXSCREENER", as_of=T0 + timedelta(seconds=5),
                                             observed_at=T0 + timedelta(seconds=5), price_usd=1,
                                             liquidity_usd=10000, status="KNOWN"))
    research_session.add(AlertRule(name="liquid", chain="bsc", cooldown_seconds=0,
                                   conditions_json=[{"field": "liquidity", "operator": ">=", "value": 1}],
                                   notification_channels=["in-app", "browser"]))
    shim = AsyncShim(research_session)
    if mode == "normal":
        for row in [pair, first, second]:
            await project(shim, row, at=AT)
    else:
        await refresh_deferred_rows(shim, [pair, first, second], at=AT)
    research_session.flush()
    assert len(research_session.scalars(select(RadarCandidateEvidence)).all()) == 3
    assert len(research_session.scalars(select(RadarAuditJob)).all()) == 1
    assert len(research_session.scalars(select(LiveSignalObservation)).all()) == 1
    assert len(research_session.scalars(select(AlertHistory)).all()) == 1
    from smms.db.models import RadarEventOutbox

    events = research_session.scalars(select(RadarEventOutbox)).all()
    assert sum(event.event_type == "CANDIDATE_DISCOVERED" for event in events) == 1
    assert sum(event.event_type == "ALERT_TRIGGERED" for event in events) == 1


def test_failed_audit_rolls_back_its_effects_but_keeps_committed_start(research_session):
    from smms.db.models import RadarEventOutbox
    from smms.radar.audit import AuditQueue, AuditWorker
    from smms.radar.engine import RadarEngine
    from smms.radar.outbox import enqueue_event

    seed(research_session)
    pair = research_session.scalar(select(Pair).where(Pair.address == PAIR))
    candidate = RadarEngine(research_session).ingest(pair, at=AT)[0]
    job = AuditQueue(research_session).schedule(candidate, reason="test", priority=1, now=AT)

    def fail(_job):
        enqueue_event(research_session, "CANDIDATE_UPDATED", chain="bsc", token=TOKEN,
                       effect_key="must-rollback", data={"bad": True})
        raise ValueError("synthetic audit failure")

    AuditWorker(research_session).process_once(fail)
    research_session.commit()
    events = research_session.scalars(select(RadarEventOutbox)).all()
    assert {event.event_type for event in events} == {"AUDIT_STARTED", "AUDIT_FAILED"}
    assert job.status.value == "RETRY"


def test_outer_rollback_keeps_only_committed_audit_start(research_session):
    from smms.db.models import RadarEventOutbox
    from smms.radar.audit import AuditQueue, AuditWorker
    from smms.radar.engine import RadarEngine
    from smms.radar.outbox import enqueue_event

    seed(research_session)
    pair = research_session.scalar(select(Pair).where(Pair.address == PAIR))
    candidate = RadarEngine(research_session).ingest(pair, at=AT)[0]
    AuditQueue(research_session).schedule(candidate, reason="test", priority=1, now=AT)
    AuditWorker(research_session).process_once(lambda _job: enqueue_event(
        research_session, "CANDIDATE_UPDATED", chain="bsc", token=TOKEN, data={"new": True}))
    research_session.rollback()
    assert [event.event_type for event in research_session.scalars(select(RadarEventOutbox))] == ["AUDIT_STARTED"]


@pytest.mark.asyncio
async def test_reorg_revalidation_enqueues_the_withdrawn_candidate(research_session):
    from smms.db.models import RadarEventOutbox, RawChainEvent
    from smms.radar.engine import RadarEngine
    from smms.radar.hooks import revalidate

    seed(research_session)
    trade = fact(research_session)
    candidate = RadarEngine(research_session).ingest(trade, at=AT)[0]
    trade.canonical, trade.removed = False, True
    raw = RawChainEvent(chain_id=56, transaction_hash=trade.tx_hash, log_index=trade.event_index,
                        block_hash=trade.block_hash, block_number=101, contract_address=PAIR,
                        topics=[], data="0x", removed=True, confirmation_status="orphaned",
                        timestamp=trade.timestamp, provider="fixture")
    research_session.add(raw)
    research_session.flush()
    await revalidate(AsyncShim(research_session), raw)
    events = research_session.scalars(select(RadarEventOutbox).where(
        RadarEventOutbox.event_type == "CANDIDATE_UPDATED")).all()
    assert candidate.status.value == "REJECTED"
    assert len(events) == 1 and events[0].data_json["status"] == "REJECTED"

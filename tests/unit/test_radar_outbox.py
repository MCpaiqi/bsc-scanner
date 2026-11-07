"""Outbox atomicity, fenced leases and restart/retry delivery."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from test_alpha_collector import coverage_session as _coverage_session
from test_derived_snapshot_integrity import T0
from test_research_integrity import research_session as _research_session

research_session, coverage_session = _research_session, _coverage_session


def enqueue(session, key="one", **kwargs):
    from smms.radar.outbox import enqueue_event

    return enqueue_event(session, "CANDIDATE_UPDATED", chain="bsc", token="token",
                         data={"status": "WATCH"}, effect_key=key, occurred_at=T0, **kwargs)


def test_enqueue_deduplicates_and_rollback_cannot_be_claimed(research_session):
    from smms.db.models import RadarEventOutbox
    from smms.radar.outbox import claim_events

    first = enqueue(research_session)
    assert enqueue(research_session).id == first.id
    assert len(research_session.scalars(select(RadarEventOutbox)).all()) == 1
    research_session.rollback()
    assert claim_events(research_session, now=T0) == []


def test_payload_is_frozen_and_cannot_be_changed(research_session):
    from smms.radar.outbox import enqueue_event

    body = {"features": {"risk": "UNKNOWN"}}
    row = enqueue_event(research_session, "RISK_CHANGED", chain="bsc", token="token", data=body)
    body["features"]["risk"] = "LOW"
    assert row.data_json["features"]["risk"] == "UNKNOWN"
    research_session.commit()
    row.data_json = {"features": {"risk": "LOW"}}
    with pytest.raises(ValueError, match="immutable"):
        research_session.flush()


def test_expired_lease_reclaims_same_event_and_fences_old_ack(research_session):
    from smms.radar.outbox import claim_events, finish_delivery

    row = enqueue(research_session)
    research_session.commit()
    first = claim_events(research_session, now=T0, limit=1)[0]
    research_session.commit()
    assert claim_events(research_session, now=T0 + timedelta(seconds=1)) == []
    second = claim_events(research_session, now=T0 + timedelta(seconds=61))[0]
    assert first.event == second.event and first.lease_token != second.lease_token
    assert not finish_delivery(research_session, first, now=T0 + timedelta(seconds=62))
    assert finish_delivery(research_session, second, now=T0 + timedelta(seconds=62))
    research_session.refresh(row)
    assert row.status == "DELIVERED" and row.attempt_count == 2


@pytest.mark.asyncio
async def test_relay_failure_retries_in_a_new_session_with_stable_envelope(coverage_session):
    from smms.db.models import RadarEventOutbox
    from smms.radar.outbox import relay_once

    await coverage_session.run_sync(enqueue)
    await coverage_session.commit()
    factory = async_sessionmaker(coverage_session.bind, expire_on_commit=False)
    delivered = []

    async def fail(event):
        delivered.append(event)
        raise OSError("synthetic transport credentials must never be persisted")

    result = await relay_once(factory, publish=fail, now=T0)
    assert result == {"claimed": 1, "delivered": 0, "failed": 1}
    row = await coverage_session.scalar(select(RadarEventOutbox).execution_options(populate_existing=True))
    assert row.status == "PENDING" and row.last_error_code == "OSError"

    async def succeed(event):
        delivered.append(event)
        return True

    assert (await relay_once(factory, publish=succeed, now=T0 + timedelta(seconds=1)))["claimed"] == 0
    assert (await relay_once(factory, publish=succeed, now=T0 + timedelta(seconds=3)))["delivered"] == 1
    assert delivered[0] == delivered[1]


@pytest.mark.asyncio
async def test_cancelled_publisher_leaves_a_recoverable_claim(coverage_session):
    from smms.radar.outbox import relay_once

    await coverage_session.run_sync(enqueue)
    await coverage_session.commit()
    factory = async_sessionmaker(coverage_session.bind, expire_on_commit=False)

    async def cancel(_event):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await relay_once(factory, publish=cancel, now=T0)

    async def succeed(_event):
        return True

    assert (await relay_once(factory, publish=succeed, now=T0 + timedelta(seconds=61)))["delivered"] == 1


def test_postgres_claims_skip_locked_and_do_not_duplicate(research_session):
    if research_session.bind.dialect.name != "postgresql":
        pytest.skip("requires independent PostgreSQL connections")
    from sqlalchemy.orm import Session

    from smms.radar.outbox import claim_events

    enqueue(research_session, "first")
    enqueue(research_session, "second")
    research_session.commit()
    first = claim_events(research_session, now=T0, limit=1)[0]
    with Session(research_session.bind) as other:
        second = claim_events(other, now=T0, limit=1)[0]
        assert first.id != second.id
        other.commit()
    research_session.commit()


@pytest.mark.asyncio
async def test_committed_relay_retries_redis_without_duplicate_local_event(coverage_session, monkeypatch):
    from smms.radar import events
    from smms.radar.outbox import relay_once

    bus = events.RadarEventBus()
    monkeypatch.setattr(events, "event_bus", bus)
    sent = []

    async def redis(event):
        sent.append(event)
        return len(sent) > 1

    monkeypatch.setattr(events, "publish_redis", redis)
    await coverage_session.run_sync(enqueue)
    await coverage_session.commit()
    factory = async_sessionmaker(coverage_session.bind, expire_on_commit=False)
    assert (await relay_once(factory, now=T0))["failed"] == 1
    assert (await relay_once(factory, now=T0 + timedelta(seconds=3)))["delivered"] == 1
    assert sent[0] == sent[1] and bus.event_count == 1


@pytest.mark.asyncio
async def test_relay_runtime_records_transport_degradation_without_stopping(monkeypatch):
    from smms import runtime
    from smms.radar import outbox

    beats = []
    monkeypatch.setattr(runtime, "get_settings", lambda: SimpleNamespace())

    async def relay(_scope):
        return {"claimed": 1, "delivered": 0, "failed": 1}

    async def beat(worker, status, **kwargs):
        beats.append((worker, status, kwargs))

    async def stop(_seconds):
        raise asyncio.CancelledError

    monkeypatch.setattr(outbox, "relay_once", relay)
    monkeypatch.setattr(runtime, "heartbeat", beat)
    monkeypatch.setattr(runtime.asyncio, "sleep", stop)
    with pytest.raises(asyncio.CancelledError):
        await runtime.run_event_relay()
    assert beats[0][0:2] == ("event-relay", "DEGRADED")


@pytest.mark.asyncio
async def test_event_delivery_audit_is_paged_and_keeps_original_envelope(coverage_session, monkeypatch):
    from contextlib import asynccontextmanager

    from smms.api import research

    for key in ["a", "b", "c"]:
        await coverage_session.run_sync(lambda sync, item=key: enqueue(sync, item))
    await coverage_session.commit()

    @asynccontextmanager
    async def scope(_settings):
        yield coverage_session

    monkeypatch.setattr(research, "session_scope", scope)
    first = await research.event_deliveries(limit=2, before_id=None)
    second = await research.event_deliveries(limit=2, before_id=first["next_cursor"])
    assert first["audit_only"] and len(first["events"]) == 2 and len(second["events"]) == 1
    assert second["events"][0]["data_json"] == {"status": "WATCH"}


@pytest.mark.asyncio
async def test_publisher_does_not_hold_the_claim_row_lock(coverage_session):
    from smms.db.models import RadarEventOutbox
    from smms.radar.outbox import relay_once

    await coverage_session.run_sync(enqueue)
    await coverage_session.commit()
    factory = async_sessionmaker(coverage_session.bind, expire_on_commit=False)

    async def publish(event):
        async with factory() as other:
            row = await other.scalar(select(RadarEventOutbox).where(
                RadarEventOutbox.event_id == event["event_id"]).with_for_update(nowait=True))
            assert row.lease_token is not None
        return True

    assert (await relay_once(factory, publish=publish, now=T0))["delivered"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["in-app", "webhook", "browser"])
async def test_manual_alert_only_enqueues_browser_events_for_browser_rules(coverage_session, monkeypatch, channel):
    from contextlib import asynccontextmanager

    from smms.api import analyst
    from smms.db.models import AlertRule, RadarEventOutbox

    rule = AlertRule(name="channel fixture", chain="bsc", notification_channels=[channel],
                     conditions_json=[{"field": "liquidity", "operator": ">=", "value": 1}])
    coverage_session.add(rule)
    await coverage_session.commit()

    @asynccontextmanager
    async def scope(_settings):
        yield coverage_session

    monkeypatch.setattr(analyst, "session_scope", scope)
    result = await analyst.evaluate_alert_rule(rule.id, analyst.RuleEvaluationPayload(
        chain="bsc", token_address="0x" + "a" * 40, state={"liquidity": 10}))
    assert result["notified"] is True
    events = (await coverage_session.scalars(select(RadarEventOutbox))).all()
    assert len(events) == (1 if channel == "browser" else 0)

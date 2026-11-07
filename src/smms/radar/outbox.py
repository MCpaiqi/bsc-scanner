"""Transactional event enqueue and bounded, fenced at-least-once relay."""

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select

from smms.db.models import RadarEventOutbox
from smms.evaluation import utc
from smms.research.lineage import json_value
from smms.research.state import insert_once

from .events import EVENT_TYPES


def enqueue_event(session, event_type, *, chain, token, data=None, effect_key=None, occurred_at=None):
    if event_type not in EVENT_TYPES:
        raise ValueError(f"unsupported radar event type: {event_type}")
    key = effect_key or uuid.uuid4().hex
    if not isinstance(key, str) or not 1 <= len(key) <= 256:
        raise ValueError("invalid outbox effect key")
    body = json.loads(json.dumps(json_value(data or {}), allow_nan=False))
    at = utc(occurred_at or datetime.now(UTC))
    insert_once(session, RadarEventOutbox, {
        "event_id": "outbox:" + uuid.uuid5(uuid.NAMESPACE_URL, key).hex, "effect_key": key,
        "event_type": event_type, "chain": chain, "token_address": token, "data_json": body,
        "occurred_at": at, "created_at": datetime.now(UTC), "available_at": at,
        "status": "PENDING", "attempt_count": 0,
    }, ["effect_key"])
    row = session.scalar(select(RadarEventOutbox).where(RadarEventOutbox.effect_key == key)
                         .execution_options(populate_existing=True))
    if (row.event_type, row.chain, row.token_address, row.data_json) != (event_type, chain, token, body):
        raise ValueError("outbox effect key reused for a different envelope")
    return row


@dataclass(frozen=True)
class Delivery:
    id: int
    lease_token: str
    event: dict


def claim_events(session, *, now, limit=16):
    now = utc(now)
    limit = min(128, max(1, limit))
    rows = session.scalars(select(RadarEventOutbox).where(
        RadarEventOutbox.status == "PENDING", RadarEventOutbox.available_at <= now,
        or_(RadarEventOutbox.lease_until.is_(None), RadarEventOutbox.lease_until <= now),
    ).order_by(RadarEventOutbox.available_at, RadarEventOutbox.id).limit(limit)
        .with_for_update(skip_locked=True).execution_options(populate_existing=True)).all()
    deliveries = []
    for row in rows:
        row.lease_token, row.lease_until = uuid.uuid4().hex, now + timedelta(seconds=60)
        row.attempt_count += 1
        deliveries.append(Delivery(row.id, row.lease_token, {
            "event_id": row.event_id, "event_type": row.event_type, "chain": row.chain,
            "token": row.token_address, "token_address": row.token_address,
            "occurred_at": utc(row.occurred_at).isoformat(),
            "data": json.loads(json.dumps(row.data_json)),
        }))
    session.flush()
    return deliveries


def finish_delivery(session, delivery, *, now, error=None):
    row = session.scalar(select(RadarEventOutbox).where(
        RadarEventOutbox.id == delivery.id, RadarEventOutbox.status == "PENDING",
        RadarEventOutbox.lease_token == delivery.lease_token,
    ).with_for_update().execution_options(populate_existing=True))
    if row is None:
        return False
    row.lease_token, row.lease_until = None, None
    if error is None:
        row.status, row.delivered_at, row.last_error_code = "DELIVERED", utc(now), None
    else:
        row.last_error_code = error[:64]
        row.available_at = utc(now) + timedelta(seconds=min(300, 2 ** min(row.attempt_count, 8)))
    session.flush()
    return True


async def publish_committed_event(event):
    from .events import event_bus, publish_redis

    published = await event_bus.publish(event["event_type"], chain=event["chain"], token=event["token"],
                                         data=event["data"], event_id=event["event_id"],
                                         occurred_at=datetime.fromisoformat(event["occurred_at"]))
    return await publish_redis(published)


async def relay_once(scope, *, publish=publish_committed_event, now=None, limit=16):
    at = utc(now or datetime.now(UTC))
    async with scope() as session:
        deliveries = await session.run_sync(lambda sync: claim_events(sync, now=at, limit=limit))
        await session.commit()
    counts = {"claimed": len(deliveries), "delivered": 0, "failed": 0}
    for delivery in deliveries:
        error = None
        try:
            if not await publish(delivery.event):
                raise RuntimeError("event transport unavailable")
        except Exception as exc:  # noqa: BLE001 - isolate delivery; cancellation leaves a recoverable lease
            error = type(exc).__name__
        completed_at = utc(now or datetime.now(UTC))
        async with scope() as session:
            accepted = await session.run_sync(lambda sync, item=delivery, code=error, at=completed_at: finish_delivery(
                sync, item, now=at, error=code,
            ))
            await session.commit()
        if accepted:
            counts["delivered" if error is None else "failed"] += 1
    return counts

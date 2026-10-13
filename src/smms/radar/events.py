"""Realtime radar event bus with bounded queues and optional Redis fanout."""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from collections import deque
from datetime import UTC, datetime
from typing import Any

from smms.config.settings import get_settings
from smms.observability import metrics, source_health_registry

EVENT_TYPES = {
    "CANDIDATE_DISCOVERED", "CANDIDATE_UPDATED", "SMART_MONEY_ENTRY",
    "EARLY_BUYER_UPDATE", "RISK_CHANGED", "SIGNAL_CHANGED",
    "AUDIT_STARTED", "AUDIT_COMPLETED", "SOURCE_DEGRADED",
    "AUDIT_FAILED", "ALERT_TRIGGERED",
    "TOKEN_DISCOVERED", "PAIR_DISCOVERED", "TRADE_BUY", "TRADE_SELL",
    "SMART_MONEY_ACTIVITY", "RISK_UPDATED", "SIGNAL_UPDATED",
    "PROVIDER_DEGRADED", "PROVIDER_RECOVERED",
}
INSTANCE_ID = os.getenv("SMMS_INSTANCE_ID", uuid.uuid4().hex)

class RadarEventBus:
    def __init__(self, max_events: int = 1000):
        self.max_events = max(1, max_events)
        self._events: deque[dict[str, Any]] = deque(maxlen=self.max_events)
        self._event_ids: set[str] = set()
        self._subscribers: set[asyncio.Queue] = set()
        self._filters: dict[asyncio.Queue, tuple[set[str] | None, str | None, str | None]] = {}
        self._lock = asyncio.Lock()
        self._counter = 0
        self._published_at: deque[float] = deque(maxlen=10_000)
        self._last_published_at: datetime | None = None
        self._last_received_at: datetime | None = None
        self._last_forwarded_at: datetime | None = None

    async def publish(self, event_type: str, *, chain: str, token: str, data: Any = None, event_id: str | None = None, occurred_at: datetime | None = None) -> dict[str, Any]:
        if event_type not in EVENT_TYPES:
            raise ValueError(f"unsupported radar event type: {event_type}")
        async with self._lock:
            if event_id is not None and event_id in self._event_ids:
                existing = next((item for item in self._events if item["event_id"] == event_id), None)
                if existing is not None:
                    return existing
            self._counter += 1
            event = {
                "event_id": event_id or f"{self._counter}-{uuid.uuid4().hex}",
                "event_type": event_type, "chain": chain, "token": token,
                "token_address": token,
                "origin_instance": INSTANCE_ID,
                "occurred_at": (occurred_at or datetime.now(UTC)).isoformat(),
                "data": data if data is not None else {},
            }
            if len(self._events) == self.max_events:
                self._event_ids.discard(self._events[0]["event_id"])
            self._events.append(event)
            self._event_ids.add(event["event_id"])
            self._published_at.append(time.monotonic())
            self._last_published_at = datetime.now(UTC)
            metrics.record_sse_event()
            metrics.record_sse_client_count(len(self._subscribers))
            source_health_registry.record_success(
                "sse", details={"client_count": len(self._subscribers)}
            )
            for queue in tuple(self._subscribers):
                types, chain_filter, token_filter = self._filters.get(queue, (None, None, None))
                if not self._matches(event, types, chain_filter, token_filter):
                    continue
                if queue.full():
                    try: queue.get_nowait()
                    except asyncio.QueueEmpty: pass
                try: queue.put_nowait(event)
                except asyncio.QueueFull: pass
            if self._subscribers:
                self._last_forwarded_at = datetime.now(UTC)
            return event

    async def subscribe(self, last_event_id: str | None = None, *, event_types: set[str] | None = None, chain: str | None = None, token: str | None = None) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=min(self.max_events, 256))
        if last_event_id:
            replay = False
            for event in self._events:
                if replay and self._matches(event, event_types, chain, token):
                    queue.put_nowait(event)
                elif event["event_id"] == last_event_id:
                    replay = True
        self._subscribers.add(queue)
        self._filters[queue] = (event_types, chain, token)
        metrics.record_sse_client_count(len(self._subscribers))
        source_health_registry.record_success(
            "sse", details={"client_count": len(self._subscribers)}
        )
        return queue

    @staticmethod
    def _matches(event, event_types, chain, token):
        return (not event_types or event["event_type"] in event_types) and (not chain or event["chain"] == chain) and (not token or event["token"] == token)

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)
        self._filters.pop(queue, None)
        metrics.record_sse_client_count(len(self._subscribers))

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    @property
    def event_count(self) -> int:
        return self._counter

    @property
    def event_rate(self) -> float:
        now = time.monotonic()
        while self._published_at and self._published_at[0] < now - 60:
            self._published_at.popleft()
        return len(self._published_at) / 60

    @property
    def last_published_at(self) -> datetime | None:
        return self._last_published_at

    def metrics_snapshot(self) -> dict[str, Any]:
        return {
            "client_count": self.subscriber_count,
            "event_count": self.event_count,
            "event_rate": self.event_rate,
            "last_event_at": self.last_published_at,
            "last_event_published_at": self.last_published_at,
            "last_event_received_at": self._last_received_at,
            "last_event_forwarded_at": self._last_forwarded_at,
        }


event_bus = RadarEventBus()

async def publish_candidate_event(event_type: str, candidate: Any, data: dict[str, Any] | None = None) -> dict[str, Any]:
    token = getattr(candidate, "token", None)
    chain = getattr(token, "chain", None) or (data or {}).get("chain", "unknown")
    address = getattr(token, "address", None) or (data or {}).get("token", "unknown")
    return await event_bus.publish(event_type, chain=chain, token=address, data=data or {})

def sse_encode(event: dict[str, Any]) -> str:
    return f"id: {event['event_id']}\nevent: {event['event_type']}\ndata: {json.dumps(event, separators=(',', ':'), default=str)}\n\n"

async def publish_redis(event: dict[str, Any]) -> bool:
    """Best-effort cross-instance fanout; canonical state remains in the DB."""
    client = None
    try:
        from redis.asyncio import Redis
        client = Redis.from_url(get_settings().redis_url, socket_connect_timeout=0.2, socket_timeout=0.2)
        payload = dict(event)
        payload.setdefault("origin_instance", INSTANCE_ID)
        await client.publish("smms:radar:events", json.dumps(payload, separators=(",", ":"), default=str))
        return True
    except (OSError, RuntimeError, TimeoutError, ConnectionError):
        source_health_registry.record_failure("redis", "redis event fanout unavailable")
        return False
    except Exception as exc:
        # redis-py raises RedisError subclasses that vary by transport/version.
        if exc.__class__.__module__.startswith("redis") or exc.__class__.__name__.endswith("RedisError"):
            source_health_registry.record_failure("redis", "redis event fanout unavailable")
            return False
        raise
    finally:
        if client is not None:
            try: await client.aclose()
            except (OSError, RuntimeError): pass


async def publish_domain_event(
    event_type: str,
    *,
    chain: str,
    token: str,
    data: Any = None,
) -> dict[str, Any]:
    """Publish locally and fan out once; Redis-originated events are local-only."""
    event = await event_bus.publish(event_type, chain=chain, token=token, data=data)
    await publish_redis(event)
    return event


def publish_domain_event_nowait(
    event_type: str, *, chain: str, token: str, data: Any = None
) -> None:
    """Dispatch from synchronous database workers without blocking their transaction."""
    coroutine = publish_domain_event(event_type, chain=chain, token=token, data=data)
    try:
        asyncio.get_running_loop().create_task(coroutine)
    except RuntimeError:
        asyncio.run(coroutine)


async def subscribe_redis(stop: asyncio.Event | None = None) -> None:
    """Bridge one Redis subscription into this instance's local SSE bus."""
    from redis.asyncio import Redis

    client = Redis.from_url(get_settings().redis_url, decode_responses=True)
    pubsub = client.pubsub()
    await pubsub.subscribe("smms:radar:events")
    try:
        while stop is None or not stop.is_set():
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if not message or message.get("type") != "message":
                continue
            payload = json.loads(message["data"])
            if payload.get("origin_instance") == INSTANCE_ID:
                continue
            await event_bus.publish(
                payload["event_type"],
                chain=payload["chain"],
                token=payload.get("token_address", payload.get("token", "unknown")),
                data=payload.get("data", {}),
                event_id=payload.get("event_id"),
                occurred_at=datetime.fromisoformat(payload["occurred_at"]),
            )
            event_bus._last_received_at = datetime.now(UTC)
    finally:
        await pubsub.unsubscribe("smms:radar:events")
        await pubsub.close()
        await client.aclose()

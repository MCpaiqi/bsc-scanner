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

class RadarSubscription(asyncio.Queue):
    """A reset replaces an untrustworthy backlog until the reader consumes it."""
    def __init__(self, maxsize):
        super().__init__(maxsize=maxsize)
        self.pending_reset = None

    def get_nowait(self):
        item = super().get_nowait()
        if item is self.pending_reset:
            self.pending_reset = None
        return item

    def reset(self, event):
        while not self.empty():
            self.get_nowait()
        self.pending_reset = event
        self.put_nowait(event)

    def advance_reset(self, cursor):
        self.pending_reset["stream_cursor"] = cursor
        self.pending_reset["data"]["resume_from"] = cursor


class RadarEventBus:
    def __init__(self, max_events: int = 1000):
        self.max_events = max(1, max_events)
        self._events: deque[dict[str, Any]] = deque(maxlen=self.max_events)
        self._event_ids: set[str] = set()
        self._subscribers: set[asyncio.Queue] = set()
        self._filters: dict[asyncio.Queue, tuple[set[str] | None, str | None, str | None]] = {}
        self._lock = asyncio.Lock()
        self._counter = 0
        self._reset_count = 0
        self._broker_available = None
        self._published_at: deque[float] = deque(maxlen=10_000)
        self._last_published_at: datetime | None = None
        self._last_received_at: datetime | None = None
        self._last_forwarded_at: datetime | None = None

    async def publish(self, event_type: str, *, chain: str, token: str, data: Any = None, event_id: str | None = None, occurred_at: datetime | None = None) -> dict[str, Any]:
        if event_type not in EVENT_TYPES:
            raise ValueError(f"unsupported radar event type: {event_type}")
        if event_id is not None and (not isinstance(event_id, str) or not event_id or len(event_id) > 256
                                     or any(character in event_id for character in ("\n", "\r", "\x00"))):
            raise ValueError("invalid event cursor")
        if event_id is not None:
            try:
                event_id.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise ValueError("invalid event cursor encoding") from exc
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
                if queue.pending_reset is not None:
                    queue.advance_reset(event["event_id"])
                elif self._matches(event, types, chain_filter, token_filter):
                    if queue.full():
                        self._reset(queue, "SLOW_CONSUMER")
                    else:
                        queue.put_nowait(event)
            if self._subscribers:
                self._last_forwarded_at = datetime.now(UTC)
            return event

    def _reset(self, queue, reason):
        _types, chain, token = self._filters.get(queue, (None, None, None))
        cursor = self._events[-1]["event_id"] if self._events else None
        if queue.pending_reset is None:
            self._reset_count += 1
        queue.reset({"event_id": "reset:" + uuid.uuid4().hex, "event_type": "STREAM_RESET",
                     "chain": chain or "bsc", "token": token or "", "token_address": token or "",
                     "origin_instance": INSTANCE_ID, "occurred_at": datetime.now(UTC).isoformat(),
                     "stream_cursor": cursor,
                     "data": {"reason": reason, "resume_from": cursor,
                              "live_available": self._broker_available,
                              "history_capacity": self.max_events, "queue_capacity": queue.maxsize}})

    async def reset_history(self, reason, *, live_available=None):
        async with self._lock:
            self._events.clear()
            self._event_ids.clear()
            if live_available is not None:
                self._broker_available = live_available
            for queue in tuple(self._subscribers):
                self._reset(queue, reason)

    async def subscribe(self, last_event_id=None, *, event_types=None, chain=None, token=None,
                        require_sync=False):
        queue = RadarSubscription(maxsize=min(self.max_events, 256))
        async with self._lock:
            self._filters[queue] = (event_types, chain, token)
            if self._broker_available is False:
                self._reset(queue, "BROKER_DISCONNECTED")
            elif last_event_id:
                history = list(self._events)
                index = next((i for i, event in enumerate(history) if event["event_id"] == last_event_id), None)
                if index is None:
                    self._reset(queue, "CURSOR_UNAVAILABLE")
                else:
                    replay = [event for event in history[index + 1:]
                              if self._matches(event, event_types, chain, token)]
                    if len(replay) > queue.maxsize:
                        self._reset(queue, "REPLAY_LIMIT")
                    else:
                        for event in replay:
                            queue.put_nowait(event)
            elif require_sync:
                self._reset(queue, "INITIAL_SYNC")
            self._subscribers.add(queue)
            metrics.record_sse_client_count(len(self._subscribers))
            source_health_registry.record_success("sse", details={"client_count": len(self._subscribers)})
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
            "reset_count": self._reset_count,
            "broker_available": self._broker_available,
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
    cursor = event.get("stream_cursor") if event["event_type"] == "STREAM_RESET" else event["event_id"]
    return f"id: {cursor or ''}\nevent: {event['event_type']}\ndata: {json.dumps(event, separators=(',', ':'), default=str)}\n\n"

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


async def subscribe_redis(stop: asyncio.Event | None = None) -> None:
    """Reconnect the bridge and invalidate replay whenever continuity is lost."""
    from redis.asyncio import Redis
    from redis.exceptions import RedisError

    while stop is None or not stop.is_set():
        client = pubsub = None
        try:
            client = Redis.from_url(get_settings().redis_url, decode_responses=False,
                                    socket_connect_timeout=1, socket_timeout=2)
            pubsub = client.pubsub()
            await pubsub.subscribe("smms:radar:events")
            await event_bus.reset_history("BROKER_RECONNECTED", live_available=True)
            source_health_registry.record_success("redis")
            while stop is None or not stop.is_set():
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if not message or message.get("type") != "message":
                    continue
                try:
                    raw = message["data"]
                    if not isinstance(raw, (str, bytes)) or len(raw) > 1_048_576:
                        raise ValueError("invalid broker frame size")
                    payload = json.loads(raw)
                    if not isinstance(payload, dict):
                        raise TypeError("invalid broker envelope")
                    if payload.get("origin_instance") == INSTANCE_ID:
                        continue
                    if (not isinstance(payload.get("chain"), str) or not payload["chain"]
                            or not isinstance(payload.get("data"), dict)
                            or not isinstance(payload.get("event_id"), str)):
                        raise ValueError("invalid broker envelope")
                    token = payload.get("token_address", payload.get("token", "unknown"))
                    if not isinstance(token, str):
                        raise TypeError("invalid broker subject")
                    await event_bus.publish(payload["event_type"], chain=payload["chain"], token=token,
                                            data=payload["data"], event_id=payload["event_id"],
                                            occurred_at=datetime.fromisoformat(payload["occurred_at"]))
                except (ValueError, TypeError, KeyError) as exc:
                    source_health_registry.record_failure("redis", type(exc).__name__)
                    await event_bus.reset_history("INVALID_BROKER_EVENT", live_available=True)
                    continue
                event_bus._last_received_at = datetime.now(UTC)
                source_health_registry.record_success("redis")
        except (RedisError, OSError, RuntimeError, UnicodeError) as exc:
            source_health_registry.record_failure("redis", type(exc).__name__)
            if event_bus._broker_available is not False:
                await event_bus.reset_history("BROKER_DISCONNECTED", live_available=False)
        finally:
            for resource in (pubsub, client):
                if resource is not None:
                    try:
                        await resource.aclose()
                    except (RedisError, OSError, RuntimeError):
                        pass
        if stop is not None:
            if stop.is_set():
                return
            try:
                await asyncio.wait_for(stop.wait(), timeout=1)
            except TimeoutError:
                pass
        else:
            await asyncio.sleep(1)

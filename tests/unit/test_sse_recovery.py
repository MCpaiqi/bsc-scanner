"""Bounded replay must disclose gaps and leave publishers nonblocking."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from smms.radar.events import RadarEventBus, sse_encode


async def publish(bus, index, **kwargs):
    return await bus.publish("CANDIDATE_UPDATED", chain="bsc", token="token",
                              event_id=f"event-{index}", data={"index": index}, **kwargs)


@pytest.mark.asyncio
async def test_replay_over_client_capacity_requires_resnapshot_without_queuefull():
    bus = RadarEventBus(max_events=1000)
    for index in range(300):
        await publish(bus, index)
    queue = await bus.subscribe("event-0")
    assert queue.qsize() == 1
    reset = queue.get_nowait()
    assert reset["event_type"] == "STREAM_RESET"
    assert reset["data"]["reason"] == "REPLAY_LIMIT"
    assert reset["data"]["resume_from"] == "event-299"
    assert sse_encode(reset).startswith("id: event-299\nevent: STREAM_RESET\n")
    bus.unsubscribe(queue)


@pytest.mark.asyncio
async def test_unknown_cursor_and_initial_connection_get_unfiltered_control():
    bus = RadarEventBus(max_events=3)
    for index in range(5):
        await publish(bus, index)
    queue = await bus.subscribe("event-0", event_types={"RISK_CHANGED"}, token="different")
    assert queue.get_nowait()["data"]["reason"] == "CURSOR_UNAVAILABLE"
    initial = await bus.subscribe(require_sync=True)
    assert initial.get_nowait()["data"]["reason"] == "INITIAL_SYNC"
    bus.unsubscribe(queue)
    bus.unsubscribe(initial)


@pytest.mark.asyncio
async def test_filtered_replay_counts_matching_events_and_stays_ordered():
    bus = RadarEventBus(max_events=1000)
    for index in range(300):
        await publish(bus, index)
    tail = await bus.publish("RISK_CHANGED", chain="bsc", token="token", event_id="risk")
    queue = await bus.subscribe("event-0", event_types={"RISK_CHANGED"})
    assert queue.qsize() == 1 and queue.get_nowait() == tail
    bus.unsubscribe(queue)


@pytest.mark.asyncio
async def test_slow_subscriber_coalesces_one_reset_until_consumed():
    bus = RadarEventBus(max_events=3)
    queue = await bus.subscribe(event_types={"CANDIDATE_UPDATED"})
    for index in range(100):
        await publish(bus, index)
    assert queue.qsize() == 1
    reset = queue.get_nowait()
    assert reset["event_type"] == "STREAM_RESET" and reset["data"]["reason"] == "SLOW_CONSUMER"
    assert reset["data"]["resume_from"] == "event-99"
    newest = await publish(bus, 100)
    assert queue.get_nowait() == newest
    bus.unsubscribe(queue)


@pytest.mark.asyncio
async def test_broker_gap_invalidates_retained_cursor_and_resets_existing_clients():
    bus = RadarEventBus()
    await publish(bus, 1)
    queue = await bus.subscribe()
    await bus.reset_history("BROKER_DISCONNECTED", live_available=False)
    assert queue.get_nowait()["data"]["live_available"] is False
    reconnected = await bus.subscribe("event-1")
    assert reconnected.get_nowait()["data"]["reason"] == "BROKER_DISCONNECTED"
    await bus.reset_history("BROKER_RECONNECTED", live_available=True)
    assert queue.get_nowait()["data"]["live_available"] is True
    bus.unsubscribe(queue)
    bus.unsubscribe(reconnected)


@pytest.mark.asyncio
async def test_subscribe_and_publish_do_not_lose_the_boundary_event():
    bus = RadarEventBus()
    await publish(bus, 0)
    queue, _event = await asyncio.gather(bus.subscribe("event-0"), publish(bus, 1))
    assert queue.qsize() == 1 and queue.get_nowait()["event_id"] == "event-1"
    bus.unsubscribe(queue)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["connection", "decode"])
async def test_broker_bridge_reconnects_skips_malformed_frames_and_closes_resources(monkeypatch, failure):
    from redis.asyncio import Redis
    from redis.exceptions import ConnectionError as RedisConnectionError

    from smms.radar import events

    stop = asyncio.Event()
    bus = RadarEventBus()
    await publish(bus, 0)
    queue = await bus.subscribe()
    clients = []
    valid = {"event_id": "remote-valid", "event_type": "CANDIDATE_UPDATED", "chain": "bsc",
             "token": "token", "origin_instance": "remote", "occurred_at": "2026-01-01T00:00:00+00:00",
             "data": {"risk": "UNKNOWN"}}

    class PubSub:
        def __init__(self, fail):
            self.fail, self.closed = fail, False
            self.messages = ["not-json", json.dumps(valid)]

        async def subscribe(self, _channel):
            if self.fail and failure == "connection":
                raise RedisConnectionError("synthetic transport failure")

        async def get_message(self, **_kwargs):
            if self.fail and failure == "decode":
                raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "synthetic decoder failure")
            payload = self.messages.pop(0)
            if not self.messages:
                stop.set()
            return {"type": "message", "data": payload}

        async def aclose(self):
            self.closed = True

    class Client:
        def __init__(self, fail):
            self.channel, self.closed = PubSub(fail), False

        def pubsub(self):
            return self.channel

        async def aclose(self):
            self.closed = True

    def connect(*_args, **_kwargs):
        client = Client(not clients)
        clients.append(client)
        return client

    monkeypatch.setattr(Redis, "from_url", connect)
    monkeypatch.setattr(events, "event_bus", bus)
    monkeypatch.setattr(events, "get_settings", lambda: SimpleNamespace(redis_url="redis://isolated.invalid"))
    await events.subscribe_redis(stop)
    assert len(clients) == 2 and all(client.closed and client.channel.closed for client in clients)
    assert [item["event_id"] for item in bus._events] == ["remote-valid"]
    assert queue.get_nowait()["data"]["live_available"] is True
    bus.unsubscribe(queue)


@pytest.mark.asyncio
@pytest.mark.parametrize("cursor", ["bad\x00cursor", "\ud800"])
async def test_non_utf8_or_nul_cursor_is_rejected_before_it_poisoned_history(cursor):
    bus = RadarEventBus()
    with pytest.raises(ValueError, match="cursor"):
        await bus.publish("CANDIDATE_UPDATED", chain="bsc", token="token", event_id=cursor)
    assert list(bus._events) == []
    queue = await bus.subscribe("unknown")
    assert sse_encode(queue.get_nowait()).encode("utf-8")
    bus.unsubscribe(queue)

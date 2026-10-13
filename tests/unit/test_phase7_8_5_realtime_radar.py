import asyncio
import json

import pytest

from smms.radar.events import RadarEventBus, sse_encode


@pytest.mark.asyncio
async def test_sse_event_format_and_last_event_id_replay():
    bus = RadarEventBus(max_events=10)
    first = await bus.publish("CANDIDATE_DISCOVERED", chain="solana", token="mint", data={"x": 1})
    second = await bus.publish("CANDIDATE_UPDATED", chain="solana", token="mint", data={"x": 2})
    encoded = sse_encode(first)
    assert encoded.startswith(f"id: {first['event_id']}\nevent: CANDIDATE_DISCOVERED\ndata: ")
    payload = json.loads(encoded.split("data: ", 1)[1].splitlines()[0])
    assert payload["event_id"] == first["event_id"]
    queue = await bus.subscribe(first["event_id"])
    replay = await asyncio.wait_for(queue.get(), 0.1)
    assert replay["event_id"] == second["event_id"]
    bus.unsubscribe(queue)


@pytest.mark.asyncio
async def test_event_filter_and_disconnect_cleanup():
    bus = RadarEventBus(max_events=4)
    queue = await bus.subscribe(event_types={"RISK_CHANGED"}, chain="bsc")
    await bus.publish("CANDIDATE_UPDATED", chain="bsc", token="a")
    await bus.publish("RISK_CHANGED", chain="solana", token="a")
    await bus.publish("RISK_CHANGED", chain="bsc", token="b")
    event = await asyncio.wait_for(queue.get(), 0.1)
    assert event["event_type"] == "RISK_CHANGED" and event["chain"] == "bsc"
    assert bus.subscriber_count == 1
    bus.unsubscribe(queue)
    assert bus.subscriber_count == 0


@pytest.mark.asyncio
async def test_slow_client_is_bounded_and_does_not_block_publishers():
    bus = RadarEventBus(max_events=3)
    queue = await bus.subscribe()
    for index in range(20):
        await bus.publish("CANDIDATE_UPDATED", chain="bsc", token=str(index))
    assert queue.qsize() <= 3
    assert len(bus._events) == 3

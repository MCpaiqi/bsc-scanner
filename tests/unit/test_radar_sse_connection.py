import asyncio

import pytest
from starlette.requests import Request

from smms.api import radar
from smms.radar.events import RadarEventBus


@pytest.mark.asyncio
async def test_quiet_sse_connection_opens_immediately_and_cleans_up(monkeypatch):
    bus = RadarEventBus()
    monkeypatch.setattr(radar, "event_bus", bus)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    request = Request({
        "type": "http", "method": "GET", "path": "/api/radar/events",
        "headers": [], "query_string": b"",
    }, receive=receive)
    response = await radar.radar_events(request)
    stream = response.body_iterator
    try:
        initial_frame = await asyncio.wait_for(anext(stream), timeout=0.25)
        assert initial_frame == ": connected\n\n"
        assert bus.subscriber_count == 1
    finally:
        await stream.aclose()
    assert bus.subscriber_count == 0

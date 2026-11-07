"""A real API process must disclose Redis gaps and resume its SSE feed."""

import asyncio
import json
import os
import socket
import sys

import httpx
import pytest

URL = os.getenv("SMMS_REDIS_TEST_URL")


@pytest.mark.asyncio
@pytest.mark.skipif(not URL, reason="explicit isolated Redis test URL required")
async def test_api_stream_recovers_from_a_real_redis_disconnect():
    from redis.asyncio import Redis

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    env = dict(os.environ, REDIS_URL=URL, SMMS_INSTANCE_ID="isolated-sse-api", ENABLE_LIVE_TRADING="false")
    process = await asyncio.create_subprocess_exec(sys.executable, "-m", "uvicorn", "smms.api.app:app",
                                                   "--host", "127.0.0.1", "--port", str(port), "--log-level", "error",
                                                   env=env, stdout=asyncio.subprocess.PIPE,
                                                   stderr=asyncio.subprocess.PIPE)
    redis = Redis.from_url(URL)
    base = f"http://127.0.0.1:{port}"

    async def frame(lines):
        async def read():
            while True:
                line = await anext(lines)
                if line.startswith("data: "):
                    return json.loads(line[6:])
        return await asyncio.wait_for(read(), timeout=8)

    async def send(event_id):
        await redis.publish("smms:radar:events", json.dumps({
            "event_id": event_id, "event_type": "RISK_CHANGED", "chain": "bsc", "token": "token",
            "origin_instance": "isolated-publisher", "occurred_at": "2026-01-01T00:00:00+00:00", "data": {},
        }))

    try:
        async with httpx.AsyncClient(base_url=base, timeout=10) as client:
            for _ in range(200):
                try:
                    response = await client.get("/openapi.json")
                    count = (await redis.pubsub_numsub("smms:radar:events"))[0][1]
                    if response.status_code == 200 and count == 1:
                        break
                except httpx.TransportError:
                    pass
                await asyncio.sleep(0.05)
            else:
                pytest.fail("isolated API process did not become ready")
            async with client.stream("GET", "/api/radar/events?event_types=RISK_CHANGED") as response:
                assert response.status_code == 200
                lines = response.aiter_lines()
                initial = await frame(lines)
                assert initial["event_type"] == "STREAM_RESET" and initial["data"]["live_available"] is True
                await send("before-gap")
                assert (await frame(lines))["event_id"] == "before-gap"
                await redis.publish("smms:radar:events", b"\xff")
                malformed = await frame(lines)
                assert malformed["event_type"] == "STREAM_RESET"
                assert malformed["data"]["reason"] == "INVALID_BROKER_EVENT"
                assert await redis.execute_command("CLIENT", "KILL", "TYPE", "pubsub") == 1
                lost = await frame(lines)
                assert lost["event_type"] == "STREAM_RESET" and lost["data"]["live_available"] is False
                restored = await frame(lines)
                assert restored["data"]["reason"] == "BROKER_RECONNECTED"
                assert restored["data"]["live_available"] is True
                async with client.stream("GET", "/api/radar/events", headers={"Last-Event-ID": "before-gap"}) as resumed:
                    reset = await frame(resumed.aiter_lines())
                    assert reset["data"]["reason"] == "CURSOR_UNAVAILABLE"
                await send("after-gap")
                assert (await frame(lines))["event_id"] == "after-gap"
    finally:
        if process.returncode is None:
            process.terminate()
        await process.communicate()
        await redis.aclose()

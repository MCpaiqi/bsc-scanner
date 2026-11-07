"""Explicit-service integration check using the shared isolated DB fixture."""

import asyncio
import json
import os
import sys
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker
from test_alpha_collector import coverage_session as _coverage_session

from smms.radar import events
from smms.radar.outbox import enqueue_event, relay_once

coverage_session = _coverage_session
REDIS_URL = os.getenv("SMMS_REDIS_TEST_URL")


@pytest.mark.asyncio
@pytest.mark.skipif(not REDIS_URL, reason="explicit isolated Redis test URL required")
async def test_rollback_stays_private_and_commit_reaches_another_process(coverage_session, monkeypatch):
    from redis.asyncio import Redis

    script = """
import asyncio, json
from smms.radar.events import event_bus, subscribe_redis
async def main():
    queue = await event_bus.subscribe()
    task = asyncio.create_task(subscribe_redis())
    try:
        while True:
            event = await asyncio.wait_for(queue.get(), timeout=20)
            if event["event_type"] != "STREAM_RESET":
                break
            if event["data"]["reason"] == "BROKER_RECONNECTED":
                print("READY", flush=True)
        print(json.dumps(event, default=str), flush=True)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        event_bus.unsubscribe(queue)
asyncio.run(main())
"""
    env = dict(os.environ, REDIS_URL=REDIS_URL, SMMS_INSTANCE_ID="isolated-api-consumer",
               ENABLE_LIVE_TRADING="false")
    process = await asyncio.create_subprocess_exec(sys.executable, "-c", script, env=env,
                                                   stdout=asyncio.subprocess.PIPE,
                                                   stderr=asyncio.subprocess.PIPE)
    client = Redis.from_url(REDIS_URL)
    monkeypatch.setattr(events, "get_settings", lambda: SimpleNamespace(redis_url=REDIS_URL))
    monkeypatch.setattr(events, "event_bus", events.RadarEventBus())
    try:
        for _attempt in range(200):
            subscribers = await client.pubsub_numsub("smms:radar:events")
            if subscribers[0][1] == 1:
                break
            await asyncio.sleep(0.05)
        else:
            pytest.fail("separate API process did not subscribe to isolated Redis")
        assert (await asyncio.wait_for(process.stdout.readline(), timeout=10)).strip() == b"READY"
        await coverage_session.run_sync(lambda sync: enqueue_event(
            sync, "CANDIDATE_UPDATED", chain="bsc", token="ghost", data={"rolled_back": True}))
        await coverage_session.rollback()
        row = await coverage_session.run_sync(lambda sync: enqueue_event(
            sync, "CANDIDATE_UPDATED", chain="bsc", token="committed", data={"committed": True}))
        event_id = row.event_id
        await coverage_session.commit()
        factory = async_sessionmaker(coverage_session.bind, expire_on_commit=False)
        assert await relay_once(factory) == {"claimed": 1, "delivered": 1, "failed": 0}
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=15)
        assert process.returncode == 0, stderr.decode(errors="replace")
        delivered = json.loads(stdout)
        assert delivered["event_id"] == event_id and delivered["token"] == "committed"
        assert delivered["data"] == {"committed": True}
    finally:
        if process.returncode is None:
            process.terminate()
            await process.communicate()
        await client.aclose()

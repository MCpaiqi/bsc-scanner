import asyncio
from datetime import UTC, datetime

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from smms.db.models import Base, RadarCandidate, Token
from smms.radar.events import RadarEventBus


def test_event_bus_deduplicates_redis_retries_and_cleans_subscriber() -> None:
    async def scenario() -> None:
        bus = RadarEventBus(max_events=4)
        queue = await bus.subscribe()
        first = await bus.publish("SIGNAL_CHANGED", chain="bsc", token="0xtoken", event_id="same-id")
        duplicate = await bus.publish("SIGNAL_CHANGED", chain="bsc", token="0xtoken", event_id="same-id")
        delivered = await queue.get()
        assert first == duplicate
        assert delivered["event_id"] == "same-id"
        assert queue.empty()
        bus.unsubscribe(queue)
        assert bus.subscriber_count == 0

    asyncio.run(scenario())


def test_radar_stats_uses_the_mapped_status_column() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        seen = datetime.now(UTC)
        token = Token(chain="bsc", address="0xstats", first_seen_at=seen)
        session.add(token)
        session.flush()
        session.add(
            RadarCandidate(
                token_id=token.id,
                discovered_at=token.first_seen_at,
                latest_seen_at=token.first_seen_at,
                discovery_source="fixture",
                discovery_reason="validation",
            )
        )
        session.flush()
        rows = session.execute(
            select(RadarCandidate._status, func.count()).group_by(RadarCandidate._status)
        ).all()
        assert rows and rows[0][1] == 1

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from smms.alpha.live import SCORE_VERSION
from smms.api.radar import _alpha_payload, _fresh_alpha_latest_rows
from smms.config import Settings
from smms.db.models import AlphaSnapshot, Base, RuntimeWorkerStatus
from smms.ingestion.bsc_stream import BSCRealtimeIngestionService


def _snapshot(*, token: str, pair: str, created: datetime, observed: int, score: int) -> AlphaSnapshot:
    return AlphaSnapshot(
        chain="bsc",
        token_address=token,
        pair_address=pair,
        pair_created_at=created,
        snapshot_at=created + timedelta(seconds=observed),
        age_seconds=observed,
        observation_seconds=observed,
        alpha_score=Decimal(score),
        score_version=SCORE_VERSION,
        scored_at=created + timedelta(seconds=observed),
        features_json={"unique_buyers": 3},
        components_json={"momentum": "5"},
        positive_reasons=["unique buyers = 3"],
        negative_reasons=[],
        warnings=["UNCALIBRATED"],
        outcome_json={},
    )


def test_fresh_rows_filter_before_limit_and_keep_one_latest_snapshot() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    now = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
    with Session(engine) as session:
        old = now - timedelta(minutes=30)
        fresh_a = now - timedelta(minutes=2)
        fresh_b = now - timedelta(minutes=1)
        session.add_all(
            [
                _snapshot(token="old", pair="old-pair", created=old, observed=300, score=99),
                _snapshot(token="fresh-a", pair="pair-a", created=fresh_a, observed=30, score=10),
                _snapshot(token="fresh-a", pair="pair-a", created=fresh_a, observed=60, score=12),
                _snapshot(token="fresh-b", pair="pair-b", created=fresh_b, observed=30, score=11),
            ]
        )
        session.commit()
        rows = _fresh_alpha_latest_rows(session, now=now, fresh_seconds=900, limit=2)
        assert [(row.token_address, row.observation_seconds) for row in rows] == [
            ("fresh-a", 60),
            ("fresh-b", 30),
        ]


def test_alpha_payload_separates_current_age_from_observation_window() -> None:
    created = datetime(2026, 9, 18, 11, 59, tzinfo=UTC)
    row = _snapshot(token="token", pair="pair", created=created, observed=30, score=42)
    payload = _alpha_payload(row, now=datetime(2026, 9, 18, 12, 3, tzinfo=UTC))
    assert payload["observation_seconds"] == 30
    assert payload["current_age_seconds"] == 240


@pytest.mark.asyncio
async def test_stale_rpc_head_degrades_and_advancing_head_recovers() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    class Stream:
        settings = Settings(_env_file=None, BSC_RPC_URL="https://fixture", BSC_HEAD_STALE_AFTER_SECONDS=30)

    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        service = BSCRealtimeIngestionService(Stream(), session=session)
        await service.record_chunk_progress(
            head_block=100,
            checkpoint_block=100,
            chunk_started_at=datetime.now(UTC),
            chunk_duration_seconds=0,
            transport="http",
            endpoint_index=0,
            counters={},
        )
        status = await session.scalar(select(RuntimeWorkerStatus).where(RuntimeWorkerStatus.worker_name == "bsc-ingestion"))
        assert status is not None and status.live_ready is False
        status.head_last_advanced_at = datetime.now(UTC) - timedelta(seconds=60)
        await session.flush()
        await service.record_chunk_progress(
            head_block=100,
            checkpoint_block=100,
            chunk_started_at=datetime.now(UTC),
            chunk_duration_seconds=0,
            transport="http",
            endpoint_index=0,
            counters={},
        )
        assert status.status == "DEGRADED"
        assert status.live_ready is False
        await service.record_chunk_progress(
            head_block=101,
            checkpoint_block=101,
            chunk_started_at=datetime.now(UTC),
            chunk_duration_seconds=0,
            transport="http",
            endpoint_index=0,
            counters={},
        )
        await service.record_chunk_progress(
            head_block=102,
            checkpoint_block=102,
            chunk_started_at=datetime.now(UTC),
            chunk_duration_seconds=0,
            transport="http",
            endpoint_index=0,
            counters={},
        )
        assert status.status == "LIVE"
        assert status.live_ready is True
    await engine.dispose()

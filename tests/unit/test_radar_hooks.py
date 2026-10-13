from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from smms.db.models import Base, RadarCandidate, Token
from smms.radar.hooks import project, refresh_deferred_rows


class _AsyncSessionShim:
    """Exercise the async hook against a real synchronous SQLAlchemy session."""

    def __init__(self, session: Session) -> None:
        self.session = session

    async def run_sync(self, callback):
        return callback(self.session)


def _candidate(session: Session, address: str) -> RadarCandidate:
    now = datetime.now(UTC)
    token = Token(chain="bsc", address=address, first_seen_at=now)
    session.add(token)
    session.flush()
    candidate = RadarCandidate(
        token_id=token.id,
        discovered_at=now,
        discovery_source="fixture",
        discovery_reason="fixture",
        latest_seen_at=now,
        features_json={},
    )
    session.add(candidate)
    session.flush()
    return candidate


@pytest.mark.asyncio
async def test_project_reads_only_candidates_for_the_event_token(monkeypatch) -> None:
    """A chain log must not scan every RadarCandidate before it is projected."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    statements: list[str] = []
    event.listen(engine, "before_cursor_execute", lambda *args: statements.append(args[2]))
    try:
        with Session(engine) as session:
            target = _candidate(session, "0xtarget")
            _candidate(session, "0xunrelated")
            monkeypatch.setattr("smms.radar.hooks.RadarEngine.ingest", lambda _engine, _row: [target])

            async def no_publish(*_args, **_kwargs) -> None:
                return None

            monkeypatch.setattr("smms.radar.events.publish_domain_event", no_publish)
            await project(_AsyncSessionShim(session), SimpleNamespace(token_address="0xtarget"))

        candidate_queries = [
            statement.lower()
            for statement in statements
            if "from radar_candidates" in statement.lower()
        ]
        assert candidate_queries
        assert all(
            "join tokens" in statement and "tokens.address in" in statement
            for statement in candidate_queries
        )
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_refresh_deferred_rows_deduplicates_by_token(monkeypatch) -> None:
    """Catch-up refreshes each affected token once after a chunk."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    calls: list[str] = []
    try:
        with Session(engine) as session:
            _candidate(session, "0xtarget")

            def refresh(_engine, row, *, at=None):
                calls.append(row.token_address)
                return []

            monkeypatch.setattr("smms.radar.hooks.RadarEngine.ingest", lambda *_args, **_kwargs: [])
            monkeypatch.setattr("smms.radar.hooks.RadarEngine.refresh_for_event", refresh)
            rows = [
                SimpleNamespace(token_address="0xtarget", chain="bsc"),
                SimpleNamespace(token_address="0xtarget", chain="bsc"),
            ]
            await refresh_deferred_rows(_AsyncSessionShim(session), rows)
        assert calls == ["0xtarget"]
    finally:
        engine.dispose()

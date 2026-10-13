from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from smms.db.models import Base, RadarCandidate, Token
from smms.radar.audit import AuditErrorCode, AuditJobStatus, AuditQueue, AuditWorker


def _session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return Session(engine)


def _candidate(session: Session, address: str = "0xtoken") -> RadarCandidate:
    token = Token(chain="bsc", address=address)
    session.add(token)
    session.flush()
    candidate = RadarCandidate(
        token_id=token.id,
        discovered_at=datetime.now(UTC),
        discovery_source="test",
        discovery_reason="test",
        latest_seen_at=datetime.now(UTC),
    )
    session.add(candidate)
    session.flush()
    return candidate


def test_priority_ordering_and_token_coalescing() -> None:
    with _session() as session:
        candidate = _candidate(session)
        queue = AuditQueue(session, cooldown_seconds=0)
        first = queue.schedule(candidate, reason="normal", priority=10)
        merged = queue.schedule(candidate, reason="smart money entry", priority=100)
        assert merged.id == first.id
        assert merged.priority == 100
        assert "smart money entry" in merged.reason
        other = _candidate(session, "0xother")
        queue.schedule(other, reason="normal", priority=50)
        assert queue.claim(worker_id="w").id == first.id


def test_retry_is_bounded_and_permanent_error_does_not_retry() -> None:
    with _session() as session:
        candidate = _candidate(session)
        queue = AuditQueue(session, cooldown_seconds=0)
        job = queue.schedule(candidate, reason="test", priority=100, max_attempts=2)
        queue.claim(worker_id="w")
        queue.fail(job, code=AuditErrorCode.TRANSIENT_ERROR, message="temporary")
        assert job.status == AuditJobStatus.RETRY
        job.next_run_at = datetime.now(UTC) - timedelta(seconds=1)
        queue.claim(worker_id="w")
        queue.fail(job, code=AuditErrorCode.TRANSIENT_ERROR, message="temporary")
        assert job.status == AuditJobStatus.FAILED
        permanent = queue.schedule(candidate, reason="again", priority=100)
        queue.claim(worker_id="w")
        queue.fail(permanent, code=AuditErrorCode.PERMANENT_ERROR, message="bad input")
        assert permanent.status == AuditJobStatus.FAILED


def test_worker_crash_recovery_and_custom_handler() -> None:
    with _session() as session:
        candidate = _candidate(session)
        queue = AuditQueue(session, cooldown_seconds=0)
        old = datetime.now(UTC) - timedelta(hours=1)
        job = queue.schedule(candidate, reason="test", priority=100, now=old)
        queue.claim(worker_id="dead", now=old)
        job.started_at = datetime.now(UTC) - timedelta(hours=1)
        assert queue.recover_crashed(timeout_seconds=60) == 1
        job.next_run_at = datetime.now(UTC) - timedelta(seconds=1)
        seen = []
        AuditWorker(session, worker_id="live").process_once(lambda item: seen.append(item.id))
        assert seen == [job.id]
        assert job.status == AuditJobStatus.SUCCEEDED


def test_worker_releases_claim_lock_before_mutating_candidate(monkeypatch) -> None:
    """Audit execution must not retain the job-row lock held by claim()."""
    with _session() as session:
        candidate = _candidate(session)
        AuditQueue(session, cooldown_seconds=0).schedule(
            candidate, reason="test", priority=100
        )
        commits: list[str] = []
        original_commit = session.commit

        def commit() -> None:
            commits.append("commit")
            original_commit()

        session.commit = commit  # type: ignore[method-assign]
        worker = AuditWorker(session, worker_id="live")
        monkeypatch.setattr(worker, "_publish_lifecycle", lambda *_args, **_kwargs: None)

        def handler(_job) -> None:
            assert commits == ["commit"]

        job = worker.process_once(handler)
        assert job is not None
        assert job.status == AuditJobStatus.SUCCEEDED

"""Durable priority audit queue for the radar slow intelligence lane."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from smms.chains.constants import BSC_CHAIN_ID
from smms.db.models import RadarAuditJob, RadarCandidate, RadarCandidateEvidence, Token
from smms.db.models.base import utc_now
from smms.observability import metrics, source_health_registry

from .features import RadarFeatureSnapshot


class AuditJobStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    RETRY = "RETRY"
    CANCELLED = "CANCELLED"


class AuditErrorCode(StrEnum):
    TRANSIENT_ERROR = "TRANSIENT_ERROR"
    PERMANENT_ERROR = "PERMANENT_ERROR"
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
    RATE_LIMIT = "RATE_LIMIT"
    INTERNAL_ERROR = "INTERNAL_ERROR"


ACTIVE_STATUSES = (AuditJobStatus.PENDING, AuditJobStatus.RUNNING, AuditJobStatus.RETRY)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


class AuditQueue:
    """PostgreSQL-backed queue operations. Redis is deliberately not involved."""

    def __init__(self, session: Session, *, cooldown_seconds: int = 60) -> None:
        self.session = session
        self.cooldown_seconds = cooldown_seconds

    def schedule(
        self,
        candidate: RadarCandidate,
        *,
        reason: str,
        priority: float,
        now: datetime | None = None,
        max_attempts: int = 3,
    ) -> RadarAuditJob:
        now = _as_utc(now or utc_now())
        token = self.session.get(Token, candidate.token_id)
        if token is None:
            raise ValueError(f"candidate {candidate.id} has no token")
        existing = self.session.scalar(
            select(RadarAuditJob)
            .where(
                RadarAuditJob.candidate_id == candidate.id,
                RadarAuditJob.status.in_(ACTIVE_STATUSES),
            )
            .order_by(RadarAuditJob.id.desc())
            .with_for_update()
        )
        if existing is not None:
            # Coalescing preserves the original attempt while making urgency observable.
            existing.priority = max(existing.priority, int(priority))
            existing.reason = f"{existing.reason};{reason}"[:256]
            existing_next_run = _as_utc(existing.next_run_at) if existing.next_run_at else now
            existing.next_run_at = min(existing_next_run, now)
            existing.updated_at = now
            return existing
        job = RadarAuditJob(
            candidate_id=candidate.id,
            chain=token.chain,
            token_address=token.address.lower(),
            reason=reason[:256],
            priority=int(priority),
            status=AuditJobStatus.PENDING,
            attempt_count=0,
            max_attempts=max(1, max_attempts),
            scheduled_at=now,
            next_run_at=now + timedelta(seconds=self.cooldown_seconds if priority < 80 else 0),
            created_at=now,
            updated_at=now,
        )
        self.session.add(job)
        self.session.flush()
        metrics.increment("audit_jobs_scheduled")
        return job

    def schedule_candidate(
        self, candidate: RadarCandidate, *, now: datetime | None = None
    ) -> RadarAuditJob:
        """Derive an explainable trigger from the fast projection before enqueueing."""
        if not isinstance(candidate.features_json, dict):
            raise TypeError("candidate features must use RadarFeatureSnapshot")
        features = RadarFeatureSnapshot.from_mapping(candidate.features_json)
        priority = float(candidate.priority_score or 0)
        if features.smart_money_entry:
            reason = "smart_money_entry"
            priority = max(priority, 100)
        elif features.risk_deteriorated:
            reason = "risk_deterioration"
            priority = max(priority, 95)
        elif (
            candidate.latest_seen_at
            and now
            and (_as_utc(now) - _as_utc(candidate.latest_seen_at)).total_seconds() > 3600
        ):
            reason = "stale_candidate"
        else:
            reason = "candidate_discovered"
        return self.schedule(candidate, reason=reason, priority=priority, now=now)

    def claim(self, *, worker_id: str, now: datetime | None = None) -> RadarAuditJob | None:
        now = _as_utc(now or utc_now())
        query = (
            select(RadarAuditJob)
            .where(
                RadarAuditJob.status.in_((AuditJobStatus.PENDING, AuditJobStatus.RETRY)),
                RadarAuditJob.scheduled_at <= now,
                or_(RadarAuditJob.next_run_at.is_(None), RadarAuditJob.next_run_at <= now),
            )
            .order_by(RadarAuditJob.priority.desc(), RadarAuditJob.scheduled_at, RadarAuditJob.id)
            .with_for_update(skip_locked=True)
        )
        job = self.session.scalar(query)
        if job is None:
            return None
        job.status = AuditJobStatus.RUNNING
        job.worker_id = worker_id
        job.started_at = now
        job.attempt_count += 1
        job.updated_at = now
        self.session.flush()
        return job

    def succeed(self, job: RadarAuditJob, *, now: datetime | None = None) -> None:
        now = _as_utc(now or utc_now())
        job.status = AuditJobStatus.SUCCEEDED
        job.completed_at = now
        job.updated_at = now
        self.session.flush()
        latency = (
            (_as_utc(job.completed_at) - _as_utc(job.started_at)).total_seconds() * 1000
            if job.started_at
            else None
        )
        metrics.record_audit(latency_ms=latency)

    def fail(
        self,
        job: RadarAuditJob,
        *,
        code: AuditErrorCode,
        message: str,
        now: datetime | None = None,
    ) -> None:
        now = _as_utc(now or utc_now())
        job.last_error_code = code.value
        job.last_error_message = message[:1000]
        retryable = code in {
            AuditErrorCode.TRANSIENT_ERROR,
            AuditErrorCode.DATA_UNAVAILABLE,
            AuditErrorCode.RATE_LIMIT,
            AuditErrorCode.INTERNAL_ERROR,
        }
        if retryable and job.attempt_count < job.max_attempts:
            job.status = AuditJobStatus.RETRY
            job.next_run_at = now + timedelta(
                seconds=min(3600, 2 ** max(0, job.attempt_count - 1) * 30)
            )
        else:
            job.status = AuditJobStatus.FAILED
            job.completed_at = now
        job.updated_at = now
        self.session.flush()
        metrics.record_audit(failed=True)

    def recover_crashed(self, *, timeout_seconds: int = 900, now: datetime | None = None) -> int:
        now = _as_utc(now or utc_now())
        threshold = now - timedelta(seconds=timeout_seconds)
        jobs = list(
            self.session.scalars(
                select(RadarAuditJob).where(
                    RadarAuditJob.status == AuditJobStatus.RUNNING,
                    RadarAuditJob.started_at < threshold,
                )
            )
        )
        for job in jobs:
            self.fail(
                job, code=AuditErrorCode.TRANSIENT_ERROR, message="worker lease expired", now=now
            )
        return len(jobs)

    def metrics(self, *, now: datetime | None = None) -> dict[str, object]:
        now = _as_utc(now or utc_now())
        counts = dict(
            self.session.execute(
                select(RadarAuditJob.status, func.count()).group_by(RadarAuditJob.status)
            ).all()
        )
        oldest = self.session.scalar(
            select(func.min(RadarAuditJob.scheduled_at)).where(
                RadarAuditJob.status.in_((AuditJobStatus.PENDING, AuditJobStatus.RETRY))
            )
        )
        completed = list(
            self.session.scalars(
                select(RadarAuditJob).where(
                    RadarAuditJob.status == AuditJobStatus.SUCCEEDED,
                    RadarAuditJob.started_at.is_not(None),
                    RadarAuditJob.completed_at.is_not(None),
                )
            )
        )
        latency_values = [
            (_as_utc(row.completed_at) - _as_utc(row.started_at)).total_seconds() * 1000
            for row in completed
        ]
        queue_depth = sum(
            counts.get(status, 0) for status in (AuditJobStatus.PENDING, AuditJobStatus.RETRY)
        )
        failure_count = counts.get(AuditJobStatus.FAILED, 0)
        completed_count = counts.get(AuditJobStatus.SUCCEEDED, 0) + failure_count
        result = {
            "queue_depth": queue_depth,
            "oldest_pending_age_seconds": max(0, (now - _as_utc(oldest)).total_seconds())
            if oldest
            else 0,
            "running_jobs": counts.get(AuditJobStatus.RUNNING, 0),
            "success_count": counts.get(AuditJobStatus.SUCCEEDED, 0),
            "retry_count": counts.get(AuditJobStatus.RETRY, 0),
            "failure_count": failure_count,
            "failure_rate": failure_count / completed_count if completed_count else 0.0,
            "total_jobs": sum(counts.values()),
            "processing_latency_ms": (sum(latency_values) / len(latency_values))
            if latency_values
            else 0,
        }
        metrics.set_gauge("audit_queue_depth", queue_depth)
        source_health_registry.record_success(
            "audit_queue",
            details={
                "queue_depth": queue_depth,
                "oldest_pending_age_seconds": result["oldest_pending_age_seconds"],
            },
        )
        if result["oldest_pending_age_seconds"] > 900 or queue_depth > 1000:
            source_health_registry.set_status(
                "audit_queue",
                "DEGRADED",
                details={"reason": "backlog"},
            )
        return result


class AuditWorker:
    def __init__(self, session: Session, *, worker_id: str = "radar-worker") -> None:
        self.session = session
        self.worker_id = worker_id
        self.queue = AuditQueue(session)

    def process_once(
        self, handler: Callable[[RadarAuditJob], None] | None = None
    ) -> RadarAuditJob | None:
        self.queue.recover_crashed()
        job = self.queue.claim(worker_id=self.worker_id)
        if job is None:
            return None
        # claim() locks the durable queue row. Release it before the audit
        # updates its candidate so canonical ingestion can coalesce that same
        # job without taking the inverse candidate -> job lock order.
        self.session.commit()
        self._publish_lifecycle("AUDIT_STARTED", job, result="RUNNING")
        try:
            (handler or self._run_audit)(job)
        except Exception as exc:  # noqa: BLE001 - persist every worker failure
            code = getattr(exc, "audit_error_code", AuditErrorCode.INTERNAL_ERROR)
            try:
                code = AuditErrorCode(code)
            except ValueError:
                code = AuditErrorCode.INTERNAL_ERROR
            self.queue.fail(job, code=code, message=str(exc))
            self._publish_lifecycle("AUDIT_FAILED", job, result=job.status.value)
        else:
            self.queue.succeed(job)
            self._publish_lifecycle("AUDIT_COMPLETED", job, result="SUCCEEDED")
        return job

    def _publish_lifecycle(
        self, event_type: str, job: RadarAuditJob, *, result: str
    ) -> None:
        from smms.radar.events import publish_domain_event_nowait

        completed = job.completed_at if event_type != "AUDIT_STARTED" else None
        duration = (
            (_as_utc(completed) - _as_utc(job.started_at)).total_seconds()
            if completed is not None and job.started_at is not None
            else None
        )
        publish_domain_event_nowait(
            event_type,
            chain=job.chain,
            token=job.token_address,
            data={
                "job_id": job.id,
                "token": job.token_address,
                "priority": job.priority,
                "started_at": job.started_at,
                "completed_at": completed,
                "duration": duration,
                "result": result,
            },
        )

    def _run_audit(self, job: RadarAuditJob) -> None:
        from smms.config.settings import get_settings
        from smms.external_evidence import (
            DexScreenerEvidenceProvider,
            EvidenceObservation,
            ExternalEvidenceOrchestrator,
            GoPlusEvidenceProvider,
        )
        from smms.radar.alerts import evaluate_enabled_rules
        from smms.radar.engine import RadarEngine
        from smms.radar.events import publish_domain_event_nowait
        from smms.radar.live_forward import record_observation
        from smms.radar.live_signal import LiveSignalService
        from smms.risk.aggregation import RiskAggregationService
        from smms.smart_money.aggregation import IntelligenceAggregationService

        now = utc_now()
        IntelligenceAggregationService(self.session, chain=job.chain).refresh_token(
            job.token_address, as_of=now
        )
        candidate = self.session.get(RadarCandidate, job.candidate_id)
        if candidate is None:
            raise ValueError(f"candidate {job.candidate_id} disappeared")
        if candidate.status.value == "REJECTED":
            return
        before_status = candidate.status
        before_signal = candidate.latest_signal_id
        before_features = dict(candidate.features_json or {})
        settings = get_settings()
        providers = []
        if settings.external_evidence_enabled and settings.dexscreener_enabled:
            providers.append(
                DexScreenerEvidenceProvider(
                    chain=job.chain,
                    base_url=settings.dexscreener_base_url,
                    timeout=settings.dexscreener_timeout_seconds,
                    ttl=settings.dexscreener_cache_ttl_seconds,
                    rate_limit=1 / settings.dexscreener_rate_limit_per_second,
                    enabled=True,
                )
            )
        if settings.external_evidence_enabled and settings.goplus_enabled:
            app_key = settings.goplus_app_key.get_secret_value() if settings.goplus_app_key else None
            app_secret = settings.goplus_app_secret.get_secret_value() if settings.goplus_app_secret else None
            providers.append(
                GoPlusEvidenceProvider(
                    chain_id=str(settings.bsc_chain_id),
                    base_url=settings.goplus_base_url,
                    timeout=settings.goplus_timeout_seconds,
                    app_key=app_key,
                    app_secret=app_secret,
                    ttl=settings.goplus_cache_ttl_seconds,
                    rate_limit=1 / settings.goplus_rate_limit_per_second,
                    enabled=settings.goplus_auth_configured,
                )
            )
        chain_observations = [
            EvidenceObservation(
                chain=job.chain,
                subject_type="TOKEN",
                subject_address=job.token_address,
                field=f"canonical_{proof.source_kind.lower()}",
                value=True,
                source="smms_chain",
                source_type="CHAIN",
                observed_at=job.started_at or now,
                fetched_at=job.started_at or now,
                confidence=1.0,
                evidence_level="ON_CHAIN_VERIFIED",
                raw_reference={"source_key": proof.source_key},
            )
            for proof in self.session.scalars(
                select(RadarCandidateEvidence).where(
                    RadarCandidateEvidence.candidate_id == candidate.id,
                    RadarCandidateEvidence.valid.is_(True),
                )
            ).all()
        ]
        resolved = ExternalEvidenceOrchestrator(
            self.session, providers=providers
        ).refresh_sync(
            job.chain,
            job.token_address,
            as_of=now,
            chain_observations=chain_observations,
        )
        RiskAggregationService(self.session, chain=job.chain).refresh(
            as_of=now,
            token_address=job.token_address,
            resolved_evidence=resolved,
        )
        RadarEngine(self.session, chain_id=BSC_CHAIN_ID).refresh(candidate, at=now)
        LiveSignalService(self.session).materialize(candidate, as_of=now)
        RadarEngine(self.session, chain_id=BSC_CHAIN_ID).refresh(candidate, at=now)
        if candidate.status != before_status or candidate.latest_signal_id != before_signal:
            record_observation(self.session, candidate, candidate.status.value, at=now)
        after_features = dict(candidate.features_json or {})
        changed = before_features != after_features or candidate.status != before_status
        if changed:
            publish_domain_event_nowait(
                "CANDIDATE_UPDATED",
                chain=job.chain,
                token=job.token_address,
                data={
                    "status": candidate.status.value,
                    "priority_score": float(candidate.priority_score),
                    "features": after_features,
                },
            )
        old_wallets = before_features.get("known_smart_wallet_count")
        new_wallets = after_features.get("known_smart_wallet_count")
        if (
            new_wallets not in {None, "UNKNOWN"}
            and (old_wallets in {None, "UNKNOWN"} or int(new_wallets) > int(old_wallets))
        ):
            publish_domain_event_nowait(
                "SMART_MONEY_ENTRY",
                chain=job.chain,
                token=job.token_address,
                data={"known_smart_wallet_count": new_wallets, "features": after_features},
            )
        if before_features.get("risk") != after_features.get("risk"):
            publish_domain_event_nowait(
                "RISK_CHANGED",
                chain=job.chain,
                token=job.token_address,
                data={"risk": after_features.get("risk"), "features": after_features},
            )
        if before_signal != candidate.latest_signal_id:
            publish_domain_event_nowait(
                "SIGNAL_CHANGED",
                chain=job.chain,
                token=job.token_address,
                data={
                    "signal_id": candidate.latest_signal_id,
                    "signal_class": candidate.status.value,
                    "features": after_features,
                },
            )
        for alert in evaluate_enabled_rules(
            self.session,
            chain=job.chain,
            token_address=job.token_address,
            state=after_features,
            at=now,
        ):
            publish_domain_event_nowait(
                "ALERT_TRIGGERED",
                chain=job.chain,
                token=job.token_address,
                data=alert,
            )

"""Durable runtime observations and worker liveness."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Index, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, utc_now


class EvidenceObservationRecord(Base):
    __tablename__ = "evidence_observations"
    __table_args__ = (
        UniqueConstraint(
            "chain", "subject_address", "field", "source", "fetched_at",
            name="uq_evidence_observation_identity",
        ),
        Index(
            "ix_evidence_observation_lookup",
            "chain", "subject_address", "field", "observed_at",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    chain: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(16), nullable=False)
    subject_address: Mapped[str] = mapped_column(String(128), nullable=False)
    field: Mapped[str] = mapped_column(String(64), nullable=False)
    value_json: Mapped[Any] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confidence: Mapped[float | None] = mapped_column(Numeric(5, 4))
    evidence_level: Mapped[str] = mapped_column(String(32), nullable=False)
    raw_reference_json: Mapped[Any] = mapped_column(JSON)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stale: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"
    __table_args__ = (
        UniqueConstraint("instance_id", "worker_type", name="uq_worker_heartbeat_instance"),
        Index("ix_worker_heartbeat_type_time", "worker_type", "last_heartbeat_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    instance_id: Mapped[str] = mapped_column(String(128), nullable=False)
    worker_type: Mapped[str] = mapped_column(String(32), nullable=False)
    last_heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="STARTING")
    current_job: Mapped[str | None] = mapped_column(String(128))
    details_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class RuntimeWorkerStatus(Base):
    """Latest cross-process worker status, keyed by logical worker name."""
    __tablename__ = "runtime_worker_status"
    __table_args__ = (Index("ix_runtime_worker_status_updated", "worker_name", "updated_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    worker_name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    instance_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    transport: Mapped[str | None] = mapped_column(String(32))
    provider: Mapped[str | None] = mapped_column(String(64))
    last_block: Mapped[int | None] = mapped_column()
    checkpoint_block: Mapped[int | None] = mapped_column()
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_raw_log_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_trade_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_market_snapshot_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_radar_update_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_event_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_event_forwarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(500))
    lag: Mapped[int | None] = mapped_column()
    # ``last_block``/``lag`` are retained for old API consumers.  These fields
    # make the live-edge contract explicit and are written by ingestion only.
    head_block: Mapped[int | None] = mapped_column(BigInteger)
    head_last_advanced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    head_staleness_seconds: Mapped[float | None] = mapped_column()
    block_lag: Mapped[int | None] = mapped_column(BigInteger)
    last_processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ingestion_phase: Mapped[str | None] = mapped_column(String(16))
    live_ready: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_chunk_metrics: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


__all__ = ["EvidenceObservationRecord", "RuntimeWorkerStatus", "WorkerHeartbeat"]

"""Revision metadata and durable reconstruction state for derived observations."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, utc_now


class SnapshotLineageMixin:
    source_epoch: Mapped[int] = mapped_column(default=0, server_default="0", nullable=False)
    research_revision: Mapped[int] = mapped_column(default=0, server_default="0", nullable=False)
    snapshot_status: Mapped[str] = mapped_column(String(24), default="VALID", server_default="VALID", nullable=False)
    previous_snapshot_id: Mapped[int | None] = mapped_column()
    input_manifest: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, server_default="{}", nullable=False)


class ResearchState(Base):
    __tablename__ = "research_states"
    chain: Mapped[str] = mapped_column(String(32), primary_key=True)
    epoch: Mapped[int] = mapped_column(default=1, nullable=False)


class ResearchEpoch(Base):
    __tablename__ = "research_epochs"
    chain: Mapped[str] = mapped_column(String(32), primary_key=True)
    epoch: Mapped[int] = mapped_column(primary_key=True)
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    cause_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class ResearchSnapshotLock(Base):
    __tablename__ = "research_snapshot_locks"
    identity: Mapped[str] = mapped_column(String(64), primary_key=True)


class ResearchSnapshotDependency(Base):
    __tablename__ = "research_snapshot_dependencies"
    __table_args__ = (Index("ix_research_dependency_source", "source_kind", "source_id"),)
    parent_kind: Mapped[str] = mapped_column(String(64), primary_key=True)
    parent_id: Mapped[int] = mapped_column(primary_key=True)
    source_kind: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_id: Mapped[int] = mapped_column(primary_key=True)
    source_digest: Mapped[str | None] = mapped_column(String(64))


class ResearchRebuildCursor(Base):
    __tablename__ = "research_rebuild_cursors"
    kind: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_id: Mapped[int] = mapped_column(default=0, nullable=False)


class ResearchRebuildJob(Base):
    __tablename__ = "research_rebuild_jobs"
    __table_args__ = (
        UniqueConstraint("kind", "snapshot_id", "requested_epoch", name="uq_research_rebuild_identity"),
        Index("ix_research_rebuild_due", "status", "next_attempt_at", "id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_id: Mapped[int] = mapped_column(nullable=False)
    requested_epoch: Mapped[int] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="PENDING", nullable=False)
    attempts: Mapped[int] = mapped_column(default=0, nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    result_id: Mapped[int | None] = mapped_column()
    last_error: Mapped[str | None] = mapped_column(String(64))

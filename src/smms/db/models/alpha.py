"""Durable work and confirmed HTTP query receipts for live Alpha research."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class AlphaCoverageJob(Base):
    __tablename__ = "alpha_coverage_jobs"
    __table_args__ = (Index("ix_alpha_coverage_due", "status", "next_attempt_at", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    pair_id: Mapped[int] = mapped_column(ForeignKey("pairs.id"), unique=True, nullable=False)
    generation: Mapped[int] = mapped_column(default=0, nullable=False)
    next_block: Mapped[int | None] = mapped_column()
    target_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="PENDING", nullable=False)
    lease_token: Mapped[str | None] = mapped_column(String(64))
    leased_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(64))


class AlphaCoverageReceipt(Base):
    __tablename__ = "alpha_coverage_receipts"
    __table_args__ = (
        UniqueConstraint("job_id", "generation", "start_block", "end_block",
                         name="uq_alpha_coverage_range"),
        Index("ix_alpha_coverage_proof", "job_id", "generation", "canonical", "start_block"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("alpha_coverage_jobs.id"), nullable=False)
    generation: Mapped[int] = mapped_column(nullable=False)
    canonical: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    start_block: Mapped[int] = mapped_column(nullable=False)
    end_block: Mapped[int] = mapped_column(nullable=False)
    start_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    end_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    start_parent_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    raw_count: Mapped[int] = mapped_column(nullable=False)
    decoded_count: Mapped[int] = mapped_column(nullable=False)
    query_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AlphaWorkCursor(Base):
    __tablename__ = "alpha_work_cursors"

    version: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_pair_id: Mapped[int] = mapped_column(default=0, nullable=False)


class AlphaBlockAnchor(Base):
    """Serialize coverage publication against canonical reconciliation, even empty scans."""

    __tablename__ = "alpha_block_anchors"

    chain_id: Mapped[int] = mapped_column(primary_key=True)
    block_number: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    block_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

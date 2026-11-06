"""Immutable forward-observation records and append-only horizon outcomes."""
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from smms.evaluation import FORWARD_EVALUATION_VERSION

from .base import Base, utc_now
from .research import SnapshotLineageMixin


class LiveSignalObservation(SnapshotLineageMixin, Base):
    __tablename__ = "live_signal_observations"
    __table_args__ = (
        UniqueConstraint("candidate_id", "signal_class", "research_revision", name="uq_live_observation_candidate_class"),
        Index("ix_live_observations_rebuild", "chain", "source_epoch", "snapshot_status", "id"),
        Index("ix_live_observations_token_time", "chain", "token_address", "observed_at"),
        Index("ix_live_observations_group", "sample_group", "observed_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    chain: Mapped[str] = mapped_column(String(32), nullable=False, default="bsc")
    candidate_id: Mapped[int] = mapped_column(ForeignKey("radar_candidates.id", ondelete="CASCADE"), nullable=False)
    token_id: Mapped[int] = mapped_column(ForeignKey("tokens.id", ondelete="CASCADE"), nullable=False)
    token_address: Mapped[str] = mapped_column(String(128), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    signal_class: Mapped[str] = mapped_column(String(32), nullable=False)
    signal_score: Mapped[Decimal | None] = mapped_column(Numeric(8, 3))
    price: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    market_cap: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    liquidity: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    smart_money_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("token_smart_money_snapshots.id", ondelete="SET NULL"))
    risk_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("token_risk_snapshots.id", ondelete="SET NULL"))
    feature_version: Mapped[str] = mapped_column(String(64), nullable=False, default="radar-features-v1")
    config_version: Mapped[str] = mapped_column(String(64), nullable=False, default="config-v1")
    model_version: Mapped[str] = mapped_column(String(64), nullable=False, default="signal-v1")
    sample_group: Mapped[str] = mapped_column(String(32), nullable=False, default="SIGNAL")
    t0_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class LiveSignalOutcome(SnapshotLineageMixin, Base):
    __tablename__ = "live_signal_outcomes"
    __table_args__ = (
        UniqueConstraint("observation_root_id", "horizon", "evaluation_version", "research_revision",
                         name="uq_live_outcome_observation_horizon_version"),
        Index("ix_live_outcomes_rebuild", "chain", "source_epoch", "snapshot_status", "id"),
        Index("ix_live_outcomes_horizon_status", "horizon", "status"),
        Index("ix_live_outcomes_due", "evaluation_version", "status", "next_evaluation_at", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    chain: Mapped[str] = mapped_column(String(32), nullable=False, default="bsc")
    observation_root_id: Mapped[int] = mapped_column(ForeignKey("live_signal_observations.id"), nullable=False)
    observation_id: Mapped[int] = mapped_column(ForeignKey("live_signal_observations.id", ondelete="CASCADE"), nullable=False)
    horizon: Mapped[str] = mapped_column(String(8), nullable=False)
    target_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    evaluation_version: Mapped[str] = mapped_column(
        String(64), nullable=False, default=FORWARD_EVALUATION_VERSION,
        server_default="legacy-v1",
    )
    next_evaluation_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda context: context.get_current_parameters()["target_at"],
    )
    endpoint_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    endpoint_offset_seconds: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    endpoint_tolerance_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=5,
                                                          server_default="5")
    provenance_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False,
                                                         default=dict, server_default="{}")
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    return_pct: Mapped[Decimal | None] = mapped_column(Numeric())
    max_return_pct: Mapped[Decimal | None] = mapped_column(Numeric())
    max_drawdown_pct: Mapped[Decimal | None] = mapped_column(Numeric())
    target_hit: Mapped[bool | None] = mapped_column(Boolean)
    stop_hit: Mapped[bool | None] = mapped_column(Boolean)
    rug: Mapped[bool | None] = mapped_column(Boolean)
    liquidity_failure: Mapped[bool | None] = mapped_column(Boolean)
    endpoint_price: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    error_code: Mapped[str | None] = mapped_column(String(32))


class ForwardEvaluationJob(Base):
    """Mutable retry state, separate from append-only research results."""

    __tablename__ = "forward_evaluation_jobs"
    __table_args__ = (
        UniqueConstraint("observation_id", "horizon", "evaluation_version", name="uq_forward_job_identity"),
        Index("ix_forward_jobs_due", "status", "next_evaluation_at", "id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    observation_id: Mapped[int] = mapped_column(ForeignKey("live_signal_observations.id"), nullable=False)
    observation_root_id: Mapped[int] = mapped_column(ForeignKey("live_signal_observations.id"), nullable=False)
    horizon: Mapped[str] = mapped_column(String(8), nullable=False)
    target_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    evaluation_version: Mapped[str] = mapped_column(String(64), nullable=False)
    next_evaluation_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    result_id: Mapped[int | None] = mapped_column(ForeignKey("live_signal_outcomes.id"))


class ForwardEvaluationCursor(Base):
    """Durable, bounded repair-sweep position for an evaluation recipe."""

    __tablename__ = "forward_evaluation_cursors"

    evaluation_version: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_observation_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0,
                                                  server_default="0")

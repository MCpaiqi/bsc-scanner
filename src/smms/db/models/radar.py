"""Persistent radar projection and its canonical evidence references."""

from datetime import datetime
from decimal import Decimal
from typing import Any, ClassVar

from sqlalchemy import JSON, Boolean, DateTime, Enum, ForeignKey, Index, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from smms.radar.domain import CandidateStatus, check_transition

from .base import Base, utc_now


def status_type(name="candidate_status"):
    return Enum(
        CandidateStatus,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        name=name,
        length=20,
    )


class RadarCandidate(Base):
    __tablename__ = "radar_candidates"
    __table_args__ = (Index("ix_radar_status_priority", "status", "priority_score"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    token_id: Mapped[int] = mapped_column(ForeignKey("tokens.id", ondelete="CASCADE"), unique=True)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    discovery_source: Mapped[str] = mapped_column(String(32))
    discovery_reason: Mapped[str] = mapped_column(String(128))
    first_seen_block: Mapped[int | None] = mapped_column(Integer)
    latest_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    _status: Mapped[CandidateStatus] = mapped_column(
        "status", status_type(), default=CandidateStatus.DISCOVERED
    )
    stage: Mapped[str] = mapped_column(String(32), default="UNKNOWN")
    priority_score: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=0)
    features_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    priority_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    latest_signal_id: Mapped[int | None] = mapped_column(
        ForeignKey("signals.id", ondelete="SET NULL")
    )
    smart_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("token_smart_money_snapshots.id", ondelete="SET NULL")
    )
    risk_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("token_risk_snapshots.id", ondelete="SET NULL")
    )
    data_status: Mapped[str] = mapped_column(String(32), default="UNKNOWN")
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    revision: Mapped[int] = mapped_column(default=1)
    __mapper_args__: ClassVar[dict] = {"version_id_col": revision}

    @property
    def status(self) -> CandidateStatus:
        return self._status

    def transition(self, new: CandidateStatus, *, reason: str, at: datetime):
        check_transition(self.status, new)
        if not reason.strip():
            raise ValueError("transition requires a reason")
        previous = self.status
        self._status = new
        self.updated_at = at
        return RadarCandidateHistory(
            candidate_id=self.id,
            previous_status=previous,
            new_status=new,
            reason=reason,
            timestamp=at,
        )


class RadarCandidateHistory(Base):
    __tablename__ = "radar_candidate_history"
    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("radar_candidates.id", ondelete="CASCADE"), index=True
    )
    previous_status: Mapped[CandidateStatus | None] = mapped_column(
        status_type("candidate_previous_status")
    )
    new_status: Mapped[CandidateStatus] = mapped_column(status_type())
    reason: Mapped[str] = mapped_column(String(128))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RadarCandidateEvidence(Base):
    __tablename__ = "radar_candidate_evidence"
    __table_args__ = (Index("uq_radar_evidence_source", "candidate_id", "source_key", unique=True),)
    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("radar_candidates.id", ondelete="CASCADE"), index=True
    )
    source_key: Mapped[str] = mapped_column(String(64))
    source_kind: Mapped[str] = mapped_column(String(32))
    pair_id: Mapped[int | None] = mapped_column(ForeignKey("pairs.id", ondelete="SET NULL"))
    lifecycle_id: Mapped[int | None] = mapped_column(
        ForeignKey("token_lifecycle_events.id", ondelete="SET NULL")
    )
    trade_id: Mapped[int | None] = mapped_column(ForeignKey("trade_events.id", ondelete="SET NULL"))
    market_id: Mapped[int | None] = mapped_column(
        ForeignKey("market_events.id", ondelete="SET NULL")
    )
    raw_event_id: Mapped[int | None] = mapped_column(
        ForeignKey("raw_chain_events.id", ondelete="SET NULL"), index=True
    )
    raw_required: Mapped[bool] = mapped_column(Boolean, default=False)
    block_hash: Mapped[str | None] = mapped_column(String(128))
    valid: Mapped[bool] = mapped_column(Boolean, default=True)

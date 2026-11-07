"""Immutable committed event envelopes with separately mutable delivery state."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, utc_now


class RadarEventOutbox(Base):
    __tablename__ = "radar_event_outbox"
    __table_args__ = (
        Index("ix_radar_outbox_due", "status", "available_at", "id"),
        CheckConstraint("status IN ('PENDING','DELIVERED')", name="ck_radar_outbox_status"),
        CheckConstraint("attempt_count >= 0", name="ck_radar_outbox_attempts"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    effect_key: Mapped[str] = mapped_column(String(256), unique=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    chain: Mapped[str] = mapped_column(String(32), nullable=False)
    token_address: Mapped[str] = mapped_column(String(128), nullable=False)
    data_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempt_count: Mapped[int] = mapped_column(default=0, nullable=False)
    lease_token: Mapped[str | None] = mapped_column(String(32))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(64))


ENVELOPE_FIELDS = ("event_id", "effect_key", "event_type", "chain", "token_address",
                   "data_json", "occurred_at", "created_at")

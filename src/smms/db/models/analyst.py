"""Analyst-owned workflow projections, separate from canonical intelligence."""

from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, utc_now


class RadarAnnotation(Base):
    """Desk-owned manual workflow state scoped to a token."""

    __tablename__ = "radar_annotations"
    __table_args__ = (UniqueConstraint("chain", "token_address", name="uq_radar_annotation_token"),
                      CheckConstraint("revision >= 1", name="ck_annotation_revision"))

    id: Mapped[int] = mapped_column(primary_key=True)
    chain: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    token_address: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    __mapper_args__: ClassVar[dict] = {"version_id_col": revision}
    favorite: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class AlertRule(Base):
    """A safe declarative rule; conditions are validated before persistence."""

    __tablename__ = "alert_rules"
    __table_args__ = (Index("ix_alert_rules_enabled", "enabled", "chain"),
        UniqueConstraint("request_id", name="uq_alert_rule_request_id"),
        CheckConstraint("token_address IS NULL OR (chain IS NOT NULL AND chain = 'bsc')", name="ck_alert_rule_target_chain"))

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    chain: Mapped[str | None] = mapped_column(String(32), index=True)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    token_address: Mapped[str | None] = mapped_column(String(128))
    request_id: Mapped[str | None] = mapped_column(String(36))

    @property
    def scope(self) -> str:
        return "TOKEN" if self.token_address is not None else "GLOBAL"

    conditions_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    match_mode: Mapped[str] = mapped_column(String(8), default="all", nullable=False)
    notification_channels: Mapped[list[str]] = mapped_column(
        JSON, default=lambda: ["in-app"], nullable=False
    )
    cooldown_seconds: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class AlertHistory(Base):
    """Durable alert evaluation and delivery history used for dedup/cooldown."""

    __tablename__ = "alert_history"
    __table_args__ = (
        Index("ix_alert_history_lookup", "chain", "token_address", "rule_id", "state_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    rule_id: Mapped[int] = mapped_column(
        ForeignKey("alert_rules.id", ondelete="CASCADE"), nullable=False
    )
    chain: Mapped[str] = mapped_column(String(32), nullable=False)
    token_address: Mapped[str] = mapped_column(String(128), nullable=False)
    state_key: Mapped[str] = mapped_column(String(64), nullable=False)
    state_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    matched: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    suppressed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(32))
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AlertNotification(Base):
    __tablename__ = "alert_notifications"
    __table_args__ = (Index("ix_alert_notifications_history", "history_id", "channel"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    history_id: Mapped[int] = mapped_column(
        ForeignKey("alert_history.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="DELIVERED", nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    delivered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

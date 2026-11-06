"""Materialized market observations with explicit provenance and freshness."""

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, BigInteger, DateTime, Index, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, utc_now

Decimal18 = Numeric(38, 18)


class TokenMarketSnapshot(Base):
    __tablename__ = "token_market_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "chain", "token_address", "pair_address", "source", "as_of",
            name="uq_token_market_snapshot_identity",
        ),
        Index("ix_token_market_snapshot_lookup", "chain", "token_address", "as_of"),
        Index("ix_token_market_snapshot_pair_time", "chain", "pair_address", "as_of"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    chain: Mapped[str] = mapped_column(String(32), nullable=False, default="bsc")
    chain_id: Mapped[int] = mapped_column(nullable=False, default=56)
    token_address: Mapped[str] = mapped_column(String(128), nullable=False)
    pair_address: Mapped[str] = mapped_column(String(128), nullable=False)
    dex: Mapped[str | None] = mapped_column(String(64))
    price_usd: Mapped[Decimal | None] = mapped_column(Decimal18)
    market_cap_usd: Mapped[Decimal | None] = mapped_column(Decimal18)
    fdv_usd: Mapped[Decimal | None] = mapped_column(Decimal18)
    liquidity_usd: Mapped[Decimal | None] = mapped_column(Decimal18)
    volume_1m: Mapped[Decimal | None] = mapped_column(Decimal18)
    volume_5m: Mapped[Decimal | None] = mapped_column(Decimal18)
    volume_h1: Mapped[Decimal | None] = mapped_column(Decimal18)
    volume_h24: Mapped[Decimal | None] = mapped_column(Decimal18)
    volume_token_1m: Mapped[Decimal | None] = mapped_column(Decimal18)
    volume_token_5m: Mapped[Decimal | None] = mapped_column(Decimal18)
    buys_1m: Mapped[int | None] = mapped_column()
    sells_1m: Mapped[int | None] = mapped_column()
    buys_5m: Mapped[int | None] = mapped_column()
    sells_5m: Mapped[int | None] = mapped_column()
    unique_buyers_1m: Mapped[int | None] = mapped_column()
    unique_buyers_5m: Mapped[int | None] = mapped_column()
    price_change_5m: Mapped[Decimal | None] = mapped_column(Decimal18)
    price_change_h1: Mapped[Decimal | None] = mapped_column(Decimal18)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    freshness_seconds: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="KNOWN")
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


__all__ = ["TokenMarketSnapshot"]


class AlphaSnapshot(Base):
    """Point-in-time experimental Alpha score and shadow outcome."""

    __tablename__ = "alpha_snapshots"
    __table_args__ = (
        UniqueConstraint("chain", "token_address", "pair_address", "observation_seconds", "score_version", name="uq_alpha_snapshot_window"),
        Index("ix_alpha_snapshot_live", "chain", "snapshot_at", "alpha_score"),
        Index("ix_alpha_snapshot_tracking", "score_version", "snapshot_at"),
        Index("ix_alpha_snapshot_due", "score_version", "task_status", "next_evaluation_at", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    chain: Mapped[str] = mapped_column(String(32), nullable=False, default="bsc")
    token_address: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    pair_address: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    pair_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    age_seconds: Mapped[int] = mapped_column(BigInteger, nullable=False)
    observation_seconds: Mapped[int] = mapped_column(nullable=False)
    alpha_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    score_version: Mapped[str] = mapped_column(String(64), nullable=False, default="experimental-v3")
    task_status: Mapped[str] = mapped_column(String(32), nullable=False, default="LEGACY", server_default="LEGACY")
    next_evaluation_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    coverage_generation: Mapped[int | None] = mapped_column()
    scored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    features_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    components_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    positive_reasons: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    negative_reasons: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    warnings: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    entry_vwap: Mapped[Decimal | None] = mapped_column(Numeric())
    current_return: Mapped[Decimal | None] = mapped_column(Numeric())
    max_tick_return: Mapped[Decimal | None] = mapped_column(Numeric())
    max_realisable_return: Mapped[Decimal | None] = mapped_column(Numeric())
    outcome_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


__all__.append("AlphaSnapshot")

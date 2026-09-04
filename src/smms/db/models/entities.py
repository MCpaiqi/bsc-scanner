from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    Enum as SqlEnum,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, utc_now

Decimal18 = Numeric(38, 18)
RawAmount = Numeric(50, 0)
Score = Numeric(6, 2)


class Chain(str, Enum):
    SOLANA = "solana"


class EventType(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    TRANSFER = "TRANSFER"
    LP = "LP"
    UNKNOWN = "UNKNOWN"


class SignalType(str, Enum):
    WATCH = "WATCH"
    INTERESTING = "INTERESTING"
    STRONG = "STRONG"
    EXCEPTIONAL = "EXCEPTIONAL"
    RISK = "RISK"


class RecordStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


class Token(TimestampMixin, Base):
    __tablename__ = "tokens"
    __table_args__ = (UniqueConstraint("chain", "address", name="uq_tokens_chain_address"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    address: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    symbol: Mapped[str | None] = mapped_column(String(32))
    name: Mapped[str | None] = mapped_column(String(128))
    chain: Mapped[str] = mapped_column(String(32), default=Chain.SOLANA.value, nullable=False)
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    initial_liquidity: Mapped[Decimal | None] = mapped_column(Decimal18)
    current_liquidity: Mapped[Decimal | None] = mapped_column(Decimal18)
    initial_market_cap: Mapped[Decimal | None] = mapped_column(Decimal18)
    current_market_cap: Mapped[Decimal | None] = mapped_column(Decimal18)
    ath_market_cap: Mapped[Decimal | None] = mapped_column(Decimal18)
    ath_multiple: Mapped[Decimal | None] = mapped_column(Decimal18)
    volume_5m: Mapped[Decimal | None] = mapped_column(Decimal18)
    volume_1h: Mapped[Decimal | None] = mapped_column(Decimal18)
    volume_24h: Mapped[Decimal | None] = mapped_column(Decimal18)
    holder_count: Mapped[int | None] = mapped_column()
    top_holder_concentration: Mapped[Decimal | None] = mapped_column(Score)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    wallet_events: Mapped[list["TokenWalletEvent"]] = relationship(
        back_populates="token", passive_deletes=True
    )
    scores: Mapped[list["TokenScore"]] = relationship(back_populates="token", passive_deletes=True)
    signals: Mapped[list["Signal"]] = relationship(back_populates="token", passive_deletes=True)
    paper_trades: Mapped[list["PaperTrade"]] = relationship(
        back_populates="token", passive_deletes=True
    )


class Wallet(TimestampMixin, Base):
    __tablename__ = "wallets"
    __table_args__ = (UniqueConstraint("chain", "address", name="uq_wallets_chain_address"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    address: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    chain: Mapped[str] = mapped_column(String(32), default=Chain.SOLANA.value, nullable=False)
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    total_transactions: Mapped[int] = mapped_column(default=0, nullable=False)
    total_swaps: Mapped[int] = mapped_column(default=0, nullable=False)
    win_rate: Mapped[Decimal | None] = mapped_column(Score)
    realized_pnl: Mapped[Decimal | None] = mapped_column(Decimal18)
    unrealized_pnl: Mapped[Decimal | None] = mapped_column(Decimal18)
    max_multiple: Mapped[Decimal | None] = mapped_column(Decimal18)
    median_multiple: Mapped[Decimal | None] = mapped_column(Decimal18)
    bot_probability: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    smart_money_score: Mapped[Decimal | None] = mapped_column(Score)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    transactions: Mapped[list["WalletTransaction"]] = relationship(
        back_populates="wallet", passive_deletes=True
    )
    token_events: Mapped[list["TokenWalletEvent"]] = relationship(
        back_populates="wallet", passive_deletes=True
    )
    scores: Mapped[list["WalletScore"]] = relationship(back_populates="wallet", passive_deletes=True)


class WalletTransaction(Base):
    __tablename__ = "wallet_transactions"
    __table_args__ = (
        UniqueConstraint("wallet_id", "signature", name="uq_wallet_transactions_wallet_signature"),
        Index("ix_wallet_transactions_timestamp", "timestamp"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallets.id", ondelete="CASCADE"), nullable=False)
    signature: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    token_in: Mapped[str | None] = mapped_column(String(128))
    token_out: Mapped[str | None] = mapped_column(String(128))
    amount_in_raw: Mapped[Decimal | None] = mapped_column(RawAmount)
    amount_out_raw: Mapped[Decimal | None] = mapped_column(RawAmount)
    amount_in: Mapped[Decimal | None] = mapped_column(Decimal18)
    amount_out: Mapped[Decimal | None] = mapped_column(Decimal18)
    usd_value: Mapped[Decimal | None] = mapped_column(Decimal18)
    side: Mapped[str | None] = mapped_column(String(8))
    dex: Mapped[str | None] = mapped_column(String(64))
    slot: Mapped[int | None] = mapped_column(BigInteger)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    wallet: Mapped[Wallet] = relationship(back_populates="transactions")


class TokenWalletEvent(Base):
    __tablename__ = "token_wallet_events"
    __table_args__ = (
        Index("ix_token_wallet_events_token_time", "token_id", "timestamp"),
        Index("ix_token_wallet_events_wallet_time", "wallet_id", "timestamp"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    token_id: Mapped[int] = mapped_column(ForeignKey("tokens.id", ondelete="CASCADE"), nullable=False)
    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallets.id", ondelete="CASCADE"), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    event_type: Mapped[EventType] = mapped_column(
        SqlEnum(EventType, native_enum=False, length=16), nullable=False
    )
    price: Mapped[Decimal | None] = mapped_column(Decimal18)
    amount_raw: Mapped[Decimal | None] = mapped_column(RawAmount)
    amount: Mapped[Decimal | None] = mapped_column(Decimal18)
    usd_value: Mapped[Decimal | None] = mapped_column(Decimal18)
    market_cap: Mapped[Decimal | None] = mapped_column(Decimal18)
    liquidity: Mapped[Decimal | None] = mapped_column(Decimal18)
    tx_signature: Mapped[str | None] = mapped_column(String(128), index=True)

    token: Mapped[Token] = relationship(back_populates="wallet_events")
    wallet: Mapped[Wallet] = relationship(back_populates="token_events")


class WalletScore(Base):
    __tablename__ = "wallet_scores"
    __table_args__ = (
        UniqueConstraint("wallet_id", "timestamp", "version", name="uq_wallet_scores_snapshot"),
        Index("ix_wallet_scores_wallet_time", "wallet_id", "timestamp"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallets.id", ondelete="CASCADE"), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    historical_return_score: Mapped[Decimal | None] = mapped_column(Score)
    win_rate_score: Mapped[Decimal | None] = mapped_column(Score)
    early_entry_score: Mapped[Decimal | None] = mapped_column(Score)
    consistency_score: Mapped[Decimal | None] = mapped_column(Score)
    activity_score: Mapped[Decimal | None] = mapped_column(Score)
    bot_penalty: Mapped[Decimal | None] = mapped_column(Score)
    total_score: Mapped[Decimal | None] = mapped_column(Score)
    version: Mapped[str] = mapped_column(String(32), default="v1", nullable=False)

    wallet: Mapped[Wallet] = relationship(back_populates="scores")


class TokenScore(Base):
    __tablename__ = "token_scores"
    __table_args__ = (
        UniqueConstraint("token_id", "timestamp", "version", name="uq_token_scores_snapshot"),
        Index("ix_token_scores_token_time", "token_id", "timestamp"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    token_id: Mapped[int] = mapped_column(ForeignKey("tokens.id", ondelete="CASCADE"), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    wallet_consensus_score: Mapped[Decimal | None] = mapped_column(Score)
    wallet_quality_score: Mapped[Decimal | None] = mapped_column(Score)
    entry_timing_score: Mapped[Decimal | None] = mapped_column(Score)
    liquidity_score: Mapped[Decimal | None] = mapped_column(Score)
    holder_score: Mapped[Decimal | None] = mapped_column(Score)
    volume_score: Mapped[Decimal | None] = mapped_column(Score)
    market_score: Mapped[Decimal | None] = mapped_column(Score)
    risk_score: Mapped[Decimal | None] = mapped_column(Score)
    total_score: Mapped[Decimal | None] = mapped_column(Score)
    version: Mapped[str] = mapped_column(String(32), default="v1", nullable=False)

    token: Mapped[Token] = relationship(back_populates="scores")


class Signal(Base):
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_id: Mapped[int] = mapped_column(ForeignKey("tokens.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    signal_type: Mapped[SignalType] = mapped_column(
        SqlEnum(SignalType, native_enum=False, length=16), nullable=False
    )
    signal_strength: Mapped[Decimal | None] = mapped_column(Score)
    wallet_count: Mapped[int] = mapped_column(default=0, nullable=False)
    weighted_wallet_count: Mapped[Decimal | None] = mapped_column(Decimal18)
    consensus_score: Mapped[Decimal | None] = mapped_column(Score)
    token_score: Mapped[Decimal | None] = mapped_column(Score)
    risk_score: Mapped[Decimal | None] = mapped_column(Score)
    entry_price: Mapped[Decimal | None] = mapped_column(Decimal18)
    market_cap: Mapped[Decimal | None] = mapped_column(Decimal18)
    status: Mapped[str] = mapped_column(String(16), default=RecordStatus.OPEN.value, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    token: Mapped[Token] = relationship(back_populates="signals")


class PaperTrade(Base):
    __tablename__ = "paper_trades"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_id: Mapped[int] = mapped_column(ForeignKey("tokens.id", ondelete="CASCADE"), nullable=False, index=True)
    signal_id: Mapped[int | None] = mapped_column(ForeignKey("signals.id", ondelete="SET NULL"))
    entry_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(Decimal18, nullable=False)
    exit_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exit_price: Mapped[Decimal | None] = mapped_column(Decimal18)
    quantity: Mapped[Decimal] = mapped_column(Decimal18, nullable=False)
    capital: Mapped[Decimal] = mapped_column(Decimal18, nullable=False)
    fees: Mapped[Decimal] = mapped_column(Decimal18, default=0, nullable=False)
    slippage: Mapped[Decimal] = mapped_column(Decimal18, default=0, nullable=False)
    realized_pnl: Mapped[Decimal | None] = mapped_column(Decimal18)
    return_pct: Mapped[Decimal | None] = mapped_column(Score)
    exit_reason: Mapped[str | None] = mapped_column(String(64))

    token: Mapped[Token] = relationship(back_populates="paper_trades")


class RawChainRecord(Base):
    __tablename__ = "raw_chain_records"
    __table_args__ = (
        UniqueConstraint("provider", "record_type", "external_id", name="uq_raw_chain_record_identity"),
        Index("ix_raw_chain_records_fetched_at", "fetched_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    record_type: Mapped[str] = mapped_column(String(64), nullable=False)
    external_id: Mapped[str] = mapped_column(String(256), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    normalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checksum: Mapped[str | None] = mapped_column(String(128))
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="RUNNING", nullable=False)
    records_seen: Mapped[int] = mapped_column(default=0, nullable=False)
    records_written: Mapped[int] = mapped_column(default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)

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
    BSC = "bsc"


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


class TradeSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class TokenStatus(str, Enum):
    NEW = "NEW"
    TRADING = "TRADING"
    NEAR_GRADUATION = "NEAR_GRADUATION"
    GRADUATED = "GRADUATED"
    DEX = "DEX"
    UNKNOWN = "UNKNOWN"


class Token(TimestampMixin, Base):
    __tablename__ = "tokens"
    __table_args__ = (UniqueConstraint("chain", "address", name="uq_tokens_chain_address"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    address: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    symbol: Mapped[str | None] = mapped_column(String(32))
    name: Mapped[str | None] = mapped_column(String(128))
    chain: Mapped[str] = mapped_column(String(32), default=Chain.SOLANA.value, nullable=False)
    launchpad: Mapped[str | None] = mapped_column(String(64), index=True)
    creator_address: Mapped[str | None] = mapped_column(String(128), index=True)
    decimals: Mapped[int | None] = mapped_column()
    total_supply: Mapped[Decimal | None] = mapped_column(RawAmount)
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_trade_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    graduation_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ath_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ath_price: Mapped[Decimal | None] = mapped_column(Decimal18)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    initial_liquidity: Mapped[Decimal | None] = mapped_column(Decimal18)
    current_liquidity: Mapped[Decimal | None] = mapped_column(Decimal18)
    initial_market_cap: Mapped[Decimal | None] = mapped_column(Decimal18)
    current_market_cap: Mapped[Decimal | None] = mapped_column(Decimal18)
    ath_market_cap: Mapped[Decimal | None] = mapped_column(Decimal18)
    graduation_liquidity: Mapped[Decimal | None] = mapped_column(Decimal18)
    ath_multiple: Mapped[Decimal | None] = mapped_column(Decimal18)
    volume_5m: Mapped[Decimal | None] = mapped_column(Decimal18)
    volume_1h: Mapped[Decimal | None] = mapped_column(Decimal18)
    volume_24h: Mapped[Decimal | None] = mapped_column(Decimal18)
    volume: Mapped[Decimal | None] = mapped_column(Decimal18)
    holder_count: Mapped[int | None] = mapped_column()
    holders: Mapped[int | None] = mapped_column()
    status: Mapped[str] = mapped_column(String(32), default=TokenStatus.UNKNOWN.value, nullable=False)
    outcome_class: Mapped[str | None] = mapped_column(String(32))
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


class TradeEvent(Base):
    """Canonical chain/launchpad-neutral trade event."""

    __tablename__ = "trade_events"
    __table_args__ = (
        UniqueConstraint("chain", "tx_hash", "event_index", name="uq_trade_events_chain_tx_index"),
        Index("ix_trade_events_token_time", "token_address", "timestamp"),
        Index("ix_trade_events_wallet_time", "trader_address", "timestamp"),
        Index("ix_trade_events_launchpad_time", "launchpad", "timestamp"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    chain: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    launchpad: Mapped[str | None] = mapped_column(String(64), index=True)
    token_address: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    trader_address: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    tx_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    block_number: Mapped[int | None] = mapped_column(BigInteger)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    side: Mapped[TradeSide] = mapped_column(
        SqlEnum(
            TradeSide,
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            length=8,
        ),
        nullable=False,
    )
    token_amount: Mapped[Decimal | None] = mapped_column(Decimal18)
    native_amount: Mapped[Decimal | None] = mapped_column(Decimal18)
    price: Mapped[Decimal | None] = mapped_column(Decimal18)
    market_cap: Mapped[Decimal | None] = mapped_column(Decimal18)
    liquidity: Mapped[Decimal | None] = mapped_column(Decimal18)
    event_index: Mapped[int] = mapped_column(default=0, nullable=False)
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class TokenLifecycleEvent(Base):
    __tablename__ = "token_lifecycle_events"
    __table_args__ = (
        UniqueConstraint(
            "chain", "token_address", "event_type", "timestamp", "tx_hash",
            name="uq_token_lifecycle_identity",
        ),
        Index("ix_token_lifecycle_events_token_time", "token_address", "timestamp"),
        Index("ix_token_lifecycle_events_launchpad_time", "launchpad", "timestamp"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    chain: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    launchpad: Mapped[str | None] = mapped_column(String(64), index=True)
    token_address: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    tx_hash: Mapped[str | None] = mapped_column(String(128))
    block_number: Mapped[int | None] = mapped_column(BigInteger)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class WalletTokenPerformance(Base):
    __tablename__ = "wallet_token_performance"
    __table_args__ = (
        UniqueConstraint("chain", "wallet_address", "token_address", name="uq_wallet_token_performance_pair"),
        Index("ix_wallet_token_performance_wallet_time", "wallet_address", "entry_time"),
        Index("ix_wallet_token_performance_token_time", "token_address", "entry_time"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    chain: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    launchpad: Mapped[str | None] = mapped_column(String(64), index=True)
    wallet_address: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    token_address: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    entry_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    entry_market_cap: Mapped[Decimal | None] = mapped_column(Decimal18)
    entry_price: Mapped[Decimal | None] = mapped_column(Decimal18)
    position_size: Mapped[Decimal | None] = mapped_column(Decimal18)
    total_buy: Mapped[Decimal | None] = mapped_column(Decimal18)
    total_sell: Mapped[Decimal | None] = mapped_column(Decimal18)
    realized_pnl: Mapped[Decimal | None] = mapped_column(Decimal18)
    unrealized_pnl: Mapped[Decimal | None] = mapped_column(Decimal18)
    max_pnl: Mapped[Decimal | None] = mapped_column(Decimal18)
    max_drawdown: Mapped[Decimal | None] = mapped_column(Decimal18)
    exit_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    holding_duration: Mapped[int | None] = mapped_column(BigInteger)
    holding_duration_seconds: Mapped[int | None] = mapped_column(BigInteger)
    roi: Mapped[Decimal | None] = mapped_column(Decimal18)
    multiple: Mapped[Decimal | None] = mapped_column(Decimal18)
    reached_2x: Mapped[bool] = mapped_column(default=False, nullable=False)
    reached_5x: Mapped[bool] = mapped_column(default=False, nullable=False)
    reached_10x: Mapped[bool] = mapped_column(default=False, nullable=False)
    reached_50x: Mapped[bool] = mapped_column(default=False, nullable=False)
    reached_100x: Mapped[bool] = mapped_column(default=False, nullable=False)


class WalletPerformance(Base):
    __tablename__ = "wallet_performance"
    __table_args__ = (
        UniqueConstraint("chain", "wallet_address", "as_of", name="uq_wallet_performance_snapshot"),
        Index("ix_wallet_performance_wallet_time", "wallet_address", "as_of"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    chain: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    wallet_address: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    tokens_traded: Mapped[int] = mapped_column(default=0, nullable=False)
    winning_tokens: Mapped[int] = mapped_column(default=0, nullable=False)
    losing_tokens: Mapped[int] = mapped_column(default=0, nullable=False)
    win_rate: Mapped[Decimal | None] = mapped_column(Score)
    avg_roi: Mapped[Decimal | None] = mapped_column(Decimal18)
    median_roi: Mapped[Decimal | None] = mapped_column(Decimal18)
    avg_multiple: Mapped[Decimal | None] = mapped_column(Decimal18)
    median_multiple: Mapped[Decimal | None] = mapped_column(Decimal18)
    risk_adjusted_return: Mapped[Decimal | None] = mapped_column(Decimal18)
    total_realized_pnl: Mapped[Decimal | None] = mapped_column(Decimal18)
    max_multiple: Mapped[Decimal | None] = mapped_column(Decimal18)
    early_entry_rate: Mapped[Decimal | None] = mapped_column(Score)
    graduation_rate: Mapped[Decimal | None] = mapped_column(Score)
    ath_capture_rate: Mapped[Decimal | None] = mapped_column(Score)
    rug_exposure: Mapped[Decimal | None] = mapped_column(Score)
    bot_probability: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    smart_money_score: Mapped[Decimal | None] = mapped_column(Score)


class SmartMoneyScore(Base):
    __tablename__ = "smart_money_scores"
    __table_args__ = (
        UniqueConstraint("chain", "wallet_address", "as_of", "version", name="uq_smart_money_score_snapshot"),
        Index("ix_smart_money_scores_wallet_time", "wallet_address", "as_of"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    chain: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    wallet_address: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    score: Mapped[Decimal] = mapped_column(Score, nullable=False)
    penalties: Mapped[Decimal] = mapped_column(Score, default=0, nullable=False)
    components_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(String(32), default="v1", nullable=False)


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

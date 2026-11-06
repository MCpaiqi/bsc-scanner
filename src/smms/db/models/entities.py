from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import (
    Enum as SqlEnum,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from smms.chains.constants import normalize_address

from .base import Base, TimestampMixin, utc_now
from .research import SnapshotLineageMixin

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
    HIGH_RISK = "HIGH_RISK"
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
    chain: Mapped[str] = mapped_column(String(32), default=Chain.BSC.value, nullable=False)
    launchpad: Mapped[str | None] = mapped_column(String(64), index=True)
    creator_address: Mapped[str | None] = mapped_column(String(128), index=True)
    creation_tx_hash: Mapped[str | None] = mapped_column(String(128), index=True)
    creation_block: Mapped[int | None] = mapped_column(BigInteger, index=True)
    creation_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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

    @validates("address")
    def normalize_address(self, _key: str, value: str) -> str:
        chain = getattr(self, "chain", None)
        return normalize_address(value, chain=chain or ("bsc" if str(value).lower().startswith("0x") else None))

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
    chain: Mapped[str] = mapped_column(String(32), default=Chain.BSC.value, nullable=False)
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

    @validates("address")
    def normalize_wallet_address(self, _key: str, value: str) -> str:
        chain = getattr(self, "chain", None)
        return normalize_address(value, chain=chain or ("bsc" if str(value).lower().startswith("0x") else None))

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
        UniqueConstraint("chain", "tx_hash", "event_index", "block_hash", name="uq_trade_events_chain_tx_index"),
        Index("ix_trade_events_token_time", "token_address", "timestamp"),
        Index("ix_trade_events_wallet_time", "trader_address", "timestamp"),
        Index("ix_trade_events_chain_trader_time", "chain", "trader_address", "timestamp"),
        Index("ix_trade_events_chain_token_time", "chain", "token_address", "timestamp"),
        Index("ix_trade_events_launchpad_time", "launchpad", "timestamp"),
        Index("ix_trade_events_chain_block", "chain", "block_number"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    chain: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    launchpad: Mapped[str | None] = mapped_column(String(64), index=True)
    chain_id: Mapped[int | None] = mapped_column(index=True)
    token_address: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    pair_address: Mapped[str | None] = mapped_column(String(128), index=True)
    base_token_address: Mapped[str | None] = mapped_column(String(128), index=True)
    trader_address: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    tx_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    event_index: Mapped[int] = mapped_column(default=0, nullable=False)
    block_number: Mapped[int | None] = mapped_column(BigInteger)
    block_hash: Mapped[str | None] = mapped_column(String(128), index=True)
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
    quote_amount: Mapped[Decimal | None] = mapped_column(Decimal18)
    token_decimals: Mapped[int | None] = mapped_column()
    base_decimals: Mapped[int | None] = mapped_column()
    quote_decimals: Mapped[int | None] = mapped_column()
    price: Mapped[Decimal | None] = mapped_column(Decimal18)
    price_quote: Mapped[Decimal | None] = mapped_column(Decimal18)
    price_usd: Mapped[Decimal | None] = mapped_column(Decimal18)
    value_quote: Mapped[Decimal | None] = mapped_column(Decimal18)
    value_usd: Mapped[Decimal | None] = mapped_column(Decimal18)
    market_cap: Mapped[Decimal | None] = mapped_column(Decimal18)
    liquidity: Mapped[Decimal | None] = mapped_column(Decimal18)
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    canonical: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    removed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    swap_sender: Mapped[str | None] = mapped_column(String(128))
    swap_recipient: Mapped[str | None] = mapped_column(String(128))
    transaction_from: Mapped[str | None] = mapped_column(String(128))
    transaction_to: Mapped[str | None] = mapped_column(String(128))
    trader_resolution: Mapped[str] = mapped_column(String(32), nullable=False, default="UNKNOWN")
    trader_confidence: Mapped[str] = mapped_column(String(16), nullable=False, default="UNKNOWN")
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    @validates("token_address", "trader_address")
    def normalize_evm_addresses(self, key: str, value: str) -> str:
        return normalize_address(value, chain=getattr(self, "chain", None) or ("bsc" if str(value).lower().startswith("0x") else None))


class EarlyBuyer(SnapshotLineageMixin, Base):
    """The first qualifying buy for a wallet/token, recomputed from trades."""

    __tablename__ = "early_buyers"
    __table_args__ = (
        UniqueConstraint("chain", "wallet_address", "token_address", "as_of", "recipe_hash", "research_revision",
                         name="uq_early_buyers_wallet_token"),
        Index("ix_early_buyers_token_rank", "token_address", "buy_rank"),
        Index("ix_early_buyers_rebuild", "chain", "source_epoch", "snapshot_status", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    recipe_hash: Mapped[str] = mapped_column(String(64), default="legacy", server_default="legacy", nullable=False)
    chain: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    wallet_address: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    token_address: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    first_buy_tx: Mapped[str] = mapped_column(String(128), nullable=False)
    first_buy_block: Mapped[int | None] = mapped_column(BigInteger)
    first_buy_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    first_buy_price: Mapped[Decimal | None] = mapped_column(Decimal18)
    first_buy_usd: Mapped[Decimal | None] = mapped_column(Decimal18)
    first_buy_market_cap: Mapped[Decimal | None] = mapped_column(Decimal18)
    market_cap_status: Mapped[str] = mapped_column(String(16), default="UNKNOWN", nullable=False)
    buy_rank: Mapped[int] = mapped_column(nullable=False)
    buy_count: Mapped[int] = mapped_column(default=1, nullable=False)
    total_buy_usd: Mapped[Decimal | None] = mapped_column(Decimal18)
    entry_delay_seconds: Mapped[int | None] = mapped_column(BigInteger)
    entry_time_bucket: Mapped[str | None] = mapped_column(String(16))
    window_type: Mapped[str] = mapped_column(String(16), default="buyers", nullable=False)
    window_value: Mapped[int] = mapped_column(default=20, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class WalletTokenPosition(SnapshotLineageMixin, Base):
    """Current wallet/token accounting snapshot derived from the full trade stream."""

    __tablename__ = "wallet_token_positions"
    __table_args__ = (
        UniqueConstraint("chain", "wallet_address", "token_address", "as_of", "recipe_hash", "research_revision",
                         name="uq_wallet_token_positions_pair"),
        Index("ix_wallet_token_positions_token", "token_address"),
        Index("ix_wallet_token_positions_rebuild", "chain", "source_epoch", "snapshot_status", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    recipe_hash: Mapped[str] = mapped_column(String(64), default="legacy", server_default="legacy", nullable=False)
    chain: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    wallet_address: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    token_address: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    cumulative_bought: Mapped[Decimal | None] = mapped_column(Decimal18)
    cumulative_sold: Mapped[Decimal | None] = mapped_column(Decimal18)
    current_balance: Mapped[Decimal | None] = mapped_column(Decimal18)
    total_invested: Mapped[Decimal | None] = mapped_column(Decimal18)
    total_returned: Mapped[Decimal | None] = mapped_column(Decimal18)
    realized_pnl: Mapped[Decimal | None] = mapped_column(Decimal18)
    unrealized_pnl: Mapped[Decimal | None] = mapped_column(Decimal18)
    roi: Mapped[Decimal | None] = mapped_column(Decimal18)
    current_price: Mapped[Decimal | None] = mapped_column(Decimal18)
    current_market_cap: Mapped[Decimal | None] = mapped_column(Decimal18)
    last_trade_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_trade_block: Mapped[int | None] = mapped_column(BigInteger)
    is_reorg_corrected: Mapped[bool] = mapped_column(default=False, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class TokenLifecycleEvent(Base):
    __tablename__ = "token_lifecycle_events"
    __table_args__ = (
        UniqueConstraint(
            "chain", "tx_hash", "event_index", name="uq_token_lifecycle_chain_tx_index"
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
    tx_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    block_number: Mapped[int | None] = mapped_column(BigInteger)
    event_index: Mapped[int] = mapped_column(default=0, nullable=False)
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
        Index("ix_wallet_performance_chain_wallet_time", "chain", "wallet_address", "as_of"),
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


class WalletSmartMoneySnapshot(SnapshotLineageMixin, Base):
    """Versioned wallet profile and score materialized at a strict cutoff."""

    __tablename__ = "wallet_smart_money_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "chain", "wallet_address", "as_of", "score_version", "profile_version", "research_revision",
            name="uq_wallet_smart_money_snapshot_version",
        ),
        Index(
            "ix_wallet_smart_money_snapshot_rank",
            "chain", "score_version", "as_of", "score",
        ),
        Index(
            "ix_wallet_smart_money_snapshot_lookup",
            "chain", "wallet_address", "score_version", "as_of",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    chain: Mapped[str] = mapped_column(String(32), nullable=False)
    wallet_address: Mapped[str] = mapped_column(String(128), nullable=False)
    score: Mapped[Decimal] = mapped_column(Score, nullable=False)
    confidence: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence_score: Mapped[Decimal] = mapped_column(Score, nullable=False)
    score_version: Mapped[str] = mapped_column(String(32), nullable=False)
    profile_version: Mapped[str] = mapped_column(String(32), nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sample_size: Mapped[int] = mapped_column(nullable=False)
    data_completeness: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    historical_coverage: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    trade_quality: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    profile_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    components_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    contributions_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    @validates("wallet_address")
    def normalize_wallet_address(self, _key: str, value: str) -> str:
        chain = getattr(self, "chain", None)
        return normalize_address(value, chain=chain or ("bsc" if str(value).lower().startswith("0x") else None))


class TokenSmartMoneySnapshot(SnapshotLineageMixin, Base):
    """Versioned token smart-money rollup materialized at a strict cutoff."""

    __tablename__ = "token_smart_money_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "chain", "token_address", "as_of", "score_version", "research_revision",
            name="uq_token_smart_money_snapshot_version",
        ),
        Index(
            "ix_token_smart_money_snapshot_lookup",
            "chain", "token_address", "score_version", "as_of",
        ),
        Index(
            "ix_token_smart_money_snapshot_rank",
            "chain", "score_version", "as_of", "smart_money_score",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    chain: Mapped[str] = mapped_column(String(32), nullable=False)
    token_address: Mapped[str] = mapped_column(String(128), nullable=False)
    smart_wallet_count: Mapped[int] = mapped_column(default=0, nullable=False)
    smart_money_buy_usd: Mapped[Decimal] = mapped_column(Decimal18, default=0, nullable=False)
    smart_money_sell_usd: Mapped[Decimal] = mapped_column(Decimal18, default=0, nullable=False)
    smart_money_net_flow: Mapped[Decimal] = mapped_column(Decimal18, default=0, nullable=False)
    early_smart_buy_count: Mapped[int] = mapped_column(default=0, nullable=False)
    smart_money_score: Mapped[Decimal | None] = mapped_column(Score)
    confidence: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence_score: Mapped[Decimal] = mapped_column(Score, nullable=False)
    data_status: Mapped[str] = mapped_column(String(32), nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    score_version: Mapped[str] = mapped_column(String(32), nullable=False)
    top_smart_wallets_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    @validates("token_address")
    def normalize_token_address(self, _key: str, value: str) -> str:
        chain = getattr(self, "chain", None)
        return normalize_address(value, chain=chain or ("bsc" if str(value).lower().startswith("0x") else None))


class TokenRiskSnapshot(SnapshotLineageMixin, Base):
    """Versioned, evidence-preserving risk snapshot independent of Smart Money."""

    __tablename__ = "token_risk_snapshots"
    __table_args__ = (
        UniqueConstraint("chain", "token_address", "as_of", "risk_version", "research_revision", name="uq_token_risk_snapshot_version"),
        Index("ix_token_risk_snapshot_lookup", "chain", "token_address", "risk_version", "as_of"),
        Index("ix_token_risk_snapshot_rank", "chain", "risk_version", "as_of", "overall_risk"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    chain: Mapped[str] = mapped_column(String(32), nullable=False)
    token_address: Mapped[str] = mapped_column(String(128), nullable=False)
    contract_risk: Mapped[str] = mapped_column(String(16), nullable=False)
    liquidity_risk: Mapped[str] = mapped_column(String(16), nullable=False)
    holder_risk: Mapped[str] = mapped_column(String(16), nullable=False)
    behavior_risk: Mapped[str] = mapped_column(String(16), nullable=False)
    bot_probability: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    wash_probability: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    overall_risk: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence: Mapped[str] = mapped_column(String(16), nullable=False)
    evidence_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    dimensions_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    risk_version: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    @validates("token_address")
    def normalize_token_address(self, _key: str, value: str) -> str:
        chain = getattr(self, "chain", None)
        return normalize_address(value, chain=chain or ("bsc" if str(value).lower().startswith("0x") else None))


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


class Signal(SnapshotLineageMixin, Base):
    __tablename__ = "signals"
    __table_args__ = (
        UniqueConstraint("token_id", "created_at", "model_version", "research_revision", name="uq_signals_token_time_version"),
        Index("ix_signals_created_model", "created_at", "model_version"),
        Index("ix_signals_rebuild", "chain", "source_epoch", "snapshot_status", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    chain: Mapped[str] = mapped_column(String(32), nullable=False, default="bsc", server_default="bsc")
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
    confidence: Mapped[Decimal | None] = mapped_column(Score)
    smart_money_score: Mapped[Decimal | None] = mapped_column(Score)
    risk_level: Mapped[str | None] = mapped_column(String(16))
    market_score: Mapped[Decimal | None] = mapped_column(Score)
    entry_market_cap: Mapped[Decimal | None] = mapped_column(Decimal18)
    entry_liquidity: Mapped[Decimal | None] = mapped_column(Decimal18)
    entry_reasons: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    risk_flags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    data_status: Mapped[str] = mapped_column(String(32), default="OK", nullable=False)
    model_version: Mapped[str] = mapped_column(String(32), default="signal-v1", nullable=False)
    config_version: Mapped[str] = mapped_column(String(32), default="config-v1", nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=RecordStatus.OPEN.value, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    token: Mapped[Token] = relationship(back_populates="signals")


class BacktestDataset(SnapshotLineageMixin, Base):
    """Immutable build metadata for a reproducible historical dataset."""

    __tablename__ = "backtest_datasets"
    __table_args__ = (
        UniqueConstraint("chain", "dataset_version", "research_revision", name="uq_backtest_datasets_version"),
        Index("ix_backtest_datasets_period", "chain", "period_from", "period_to"),
        Index("ix_backtest_datasets_rebuild", "chain", "source_epoch", "snapshot_status", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_version: Mapped[str] = mapped_column(String(64), nullable=False)
    build_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    period_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_to: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    chain: Mapped[str] = mapped_column(String(32), nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_coverage_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    report_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class BacktestDatasetRow(Base):
    """Serialized point-in-time row belonging to a materialized dataset."""

    __tablename__ = "backtest_dataset_rows"
    __table_args__ = (
        UniqueConstraint("dataset_id", "token", "signal_timestamp", name="uq_backtest_dataset_rows_identity"),
        Index("ix_backtest_dataset_rows_signal_time", "signal_timestamp"),
        Index("ix_backtest_dataset_rows_dataset_token", "dataset_id", "token"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("backtest_datasets.id", ondelete="CASCADE"), nullable=False)
    token: Mapped[str] = mapped_column(String(128), nullable=False)
    signal_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    discovery_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


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


class RawChainEvent(Base):
    """Replayable raw EVM log captured by the realtime chain stream."""

    __tablename__ = "raw_chain_events"
    __table_args__ = (
        UniqueConstraint(
            "chain_id",
            "transaction_hash",
            "log_index",
            "block_hash",
            name="uq_raw_chain_events_identity",
        ),
        Index(
            "ux_raw_chain_events_identity_coalesced",
            "chain_id",
            "transaction_hash",
            "log_index",
            text("coalesce(block_hash, '')"),
            unique=True,
        ),
        Index("ix_raw_chain_events_block", "chain_id", "block_number"),
        Index("ix_raw_chain_events_status", "confirmation_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    chain_id: Mapped[int] = mapped_column(nullable=False)
    block_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    block_hash: Mapped[str | None] = mapped_column(String(128))
    transaction_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    transaction_index: Mapped[int | None] = mapped_column()
    log_index: Mapped[int] = mapped_column(nullable=False)
    contract_address: Mapped[str] = mapped_column(String(128), nullable=False)
    topics: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    data: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ingestion_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    confirmation_status: Mapped[str] = mapped_column(
        String(16), default="pending", nullable=False
    )
    removed: Mapped[bool] = mapped_column(default=False, nullable=False)
    raw_payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class StreamCheckpoint(Base):
    """Durable cursor for a provider stream, scoped by chain and stream name."""

    __tablename__ = "stream_checkpoints"
    __table_args__ = (
        UniqueConstraint(
            "chain_id", "provider", "stream_name", name="uq_stream_checkpoints_identity"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    chain_id: Mapped[int] = mapped_column(nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    stream_name: Mapped[str] = mapped_column(String(128), nullable=False)
    last_processed_block: Mapped[int | None] = mapped_column(BigInteger)
    last_processed_log: Mapped[int | None] = mapped_column()
    last_processed_transaction_hash: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="stopped", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class Pair(Base):
    """Canonical DEX pair discovered from an on-chain factory event."""

    __tablename__ = "pairs"
    __table_args__ = (
        UniqueConstraint("chain_id", "address", name="uq_pairs_chain_address"),
        Index("ix_pairs_token0_token1", "token0_address", "token1_address"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    chain_id: Mapped[int] = mapped_column(nullable=False, index=True)
    address: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    token0_address: Mapped[str] = mapped_column(String(128), nullable=False)
    token1_address: Mapped[str] = mapped_column(String(128), nullable=False)
    factory_address: Mapped[str | None] = mapped_column(String(128), index=True)
    dex: Mapped[str] = mapped_column(String(64), nullable=False, default="unknown")
    quote_token: Mapped[str | None] = mapped_column(String(128), index=True)
    creation_tx_hash: Mapped[str | None] = mapped_column(String(128), index=True)
    creation_block: Mapped[int | None] = mapped_column(BigInteger, index=True)
    creation_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    creation_block_hash: Mapped[str | None] = mapped_column(String(128), index=True)
    canonical: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    removed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    current_liquidity: Mapped[Decimal | None] = mapped_column(Decimal18)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class MarketEvent(Base):
    """Unified, replayable market event emitted by protocol decoders."""

    __tablename__ = "market_events"
    __table_args__ = (
        UniqueConstraint(
            "chain_id", "tx_hash", "event_index", "block_hash", name="uq_market_events_identity"
        ),
        Index("ix_market_events_token_time", "token_address", "timestamp"),
        Index("ix_market_events_pair_time", "pair_address", "timestamp"),
        Index("ix_market_events_chain_block", "chain_id", "block_number"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    chain_id: Mapped[int] = mapped_column(nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    token_address: Mapped[str | None] = mapped_column(String(128), index=True)
    pair_address: Mapped[str | None] = mapped_column(String(128), index=True)
    wallet_address: Mapped[str | None] = mapped_column(String(128), index=True)
    tx_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    event_index: Mapped[int] = mapped_column(default=0, nullable=False)
    block_number: Mapped[int | None] = mapped_column(BigInteger, index=True)
    block_hash: Mapped[str | None] = mapped_column(String(128), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    side: Mapped[str | None] = mapped_column(String(16), index=True)
    # PancakeSwap decoder values are raw uint256 units; keep their full integer
    # precision here. TradeEvent stores the corresponding human-unit values.
    token_amount: Mapped[Decimal | None] = mapped_column(RawAmount)
    quote_amount: Mapped[Decimal | None] = mapped_column(RawAmount)
    usd_value: Mapped[Decimal | None] = mapped_column(Decimal18)
    price: Mapped[Decimal | None] = mapped_column(Decimal18)
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

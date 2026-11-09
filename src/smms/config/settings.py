import re
from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

from smms.chains.constants import BSC_CHAIN_ID, DEFAULT_CHAIN
from smms.radar.config import RadarConfig


class Settings(BaseSettings):
    """Environment-backed application settings with paper-first safety defaults."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore",
                                     hide_input_in_errors=True)

    radar: RadarConfig = Field(default_factory=RadarConfig, alias="RADAR_CONFIG")

    app_env: str = Field("development", alias="APP_ENV")
    log_level: str = Field("INFO", alias="LOG_LEVEL")
    database_url: str = Field(
        "postgresql+asyncpg://postgres@127.0.0.1:5432/smms", alias="DATABASE_URL", repr=False, exclude=True
    )
    database_host: str | None = Field(None, alias="DATABASE_HOST")
    database_port: int = Field(5432, alias="DATABASE_PORT", ge=1, le=65535)
    database_name: str = Field("smms", alias="DATABASE_NAME", min_length=1)
    database_user: str = Field("postgres", alias="DATABASE_USER", min_length=1)
    database_password: SecretStr | None = Field(None,
        validation_alias=AliasChoices("DATABASE_PASSWORD", "SMMS_POSTGRES_PASSWORD"), repr=False, exclude=True)
    analyst_key: SecretStr | None = Field(None, alias="SMMS_ANALYST_KEY", repr=False, exclude=True)
    redis_url: str = Field("redis://localhost:6379/0", alias="REDIS_URL")
    bsc_rpc_url: str | None = Field(None, alias="BSC_RPC_URL")
    bsc_rpc_urls: str | None = Field(None, alias="BSC_RPC_URLS")
    bsc_ws_url: str | None = Field(None, alias="BSC_WS_URL")
    bsc_ws_urls: str | None = Field(None, alias="BSC_WS_URLS")
    bsc_chain_id: int = Field(BSC_CHAIN_ID, alias="BSC_CHAIN_ID", ge=1)
    bsc_rpc_timeout_seconds: float = Field(10, alias="BSC_RPC_TIMEOUT_SECONDS", gt=0)
    bsc_rpc_max_retries: int = Field(2, alias="BSC_RPC_MAX_RETRIES", ge=0)
    bsc_rpc_retry_backoff_seconds: float = Field(0.25, alias="BSC_RPC_RETRY_BACKOFF_SECONDS", ge=0)
    bsc_log_chunk_size: int = Field(10_000, alias="BSC_LOG_CHUNK_SIZE", gt=0)
    bsc_log_address_batch_size: int = Field(4, alias="BSC_LOG_ADDRESS_BATCH_SIZE", gt=0)
    bsc_catchup_rpc_concurrency: int = Field(
        8, alias="BSC_CATCHUP_RPC_CONCURRENCY", ge=1, le=32
    )
    bsc_ws_connect_timeout_seconds: float = Field(
        10,
        validation_alias=AliasChoices("BSC_WS_TIMEOUT_SECONDS", "BSC_WS_CONNECT_TIMEOUT_SECONDS"),
        serialization_alias="BSC_WS_TIMEOUT_SECONDS",
        gt=0,
    )
    bsc_ws_reconnect_max_retries: int = Field(
        5,
        validation_alias=AliasChoices("BSC_WS_MAX_RETRIES", "BSC_WS_RECONNECT_MAX_RETRIES"),
        serialization_alias="BSC_WS_MAX_RETRIES",
        ge=0,
    )
    bsc_ws_backoff_seconds: float = Field(0.5, alias="BSC_WS_BACKOFF_SECONDS", ge=0)
    bsc_ws_backoff_max_seconds: float = Field(30, alias="BSC_WS_BACKOFF_MAX_SECONDS", gt=0)
    bsc_ws_jitter_seconds: float = Field(0.25, alias="BSC_WS_JITTER_SECONDS", ge=0)
    bsc_ws_heartbeat_seconds: float = Field(20, alias="BSC_WS_HEARTBEAT_SECONDS", gt=0)
    bsc_confirmation_depth: int = Field(3, alias="BSC_CONFIRMATION_DEPTH", ge=0)
    bsc_poll_interval_seconds: float = Field(2, alias="BSC_POLL_INTERVAL_SECONDS", gt=0)
    bsc_reorg_lookback_blocks: int = Field(12, alias="BSC_REORG_LOOKBACK_BLOCKS", ge=0)
    canonical_anchor_hot_blocks: int = Field(4096, alias="CANONICAL_ANCHOR_HOT_BLOCKS", ge=512, le=10_000_000)
    # A live edge is an epoch, not a historical recovery cursor.  Changing
    # this name deliberately starts a fresh, safe-head checkpoint while the
    # historical ``bsc_realtime`` lane remains intact.
    bsc_live_tail_stream_name: str = Field(
        "bsc_realtime_live_tail_v2", alias="BSC_LIVE_TAIL_STREAM_NAME", min_length=1, max_length=128
    )
    bsc_dex_factory_addresses: str | None = Field(None, alias="BSC_DEX_FACTORY_ADDRESSES")
    bsc_quote_tokens: str | None = Field(None, alias="BSC_QUOTE_TOKENS")
    bsc_max_active_pairs: int = Field(500, alias="BSC_MAX_ACTIVE_PAIRS", ge=1)
    bsc_pair_activity_ttl_seconds: int = Field(3600, alias="BSC_PAIR_ACTIVITY_TTL_SECONDS", ge=1)
    bsc_pair_subscription_batch_size: int = Field(50, alias="BSC_PAIR_SUBSCRIPTION_BATCH_SIZE", ge=1)
    runtime_heartbeat_interval_seconds: float = Field(
        15, alias="RUNTIME_HEARTBEAT_INTERVAL_SECONDS", gt=0
    )
    bsc_live_lag_threshold: int = Field(3, alias="BSC_LIVE_LAG_THRESHOLD", ge=0)
    bsc_live_stable_chunks: int = Field(2, alias="BSC_LIVE_STABLE_CHUNKS", ge=1)
    bsc_head_stale_after_seconds: float = Field(
        30, alias="BSC_HEAD_STALE_AFTER_SECONDS", gt=0
    )
    transaction_context_cache_ttl_seconds: float = Field(
        300, alias="TRANSACTION_CONTEXT_CACHE_TTL_SECONDS", ge=0
    )
    transaction_context_cache_max_entries: int = Field(
        10_000, alias="TRANSACTION_CONTEXT_CACHE_MAX_ENTRIES", ge=1
    )
    market_snapshot_interval_seconds: int = Field(30, alias="MARKET_SNAPSHOT_INTERVAL_SECONDS", ge=1)
    alpha_min_future_quote_notional: float = Field(
        0.01, alias="ALPHA_MIN_FUTURE_QUOTE_NOTIONAL", gt=0
    )
    alpha_fresh_seconds: int = Field(900, alias="ALPHA_FRESH_SECONDS", ge=1)
    early_buyer_window_seconds: int = Field(3600, alias="EARLY_BUYER_WINDOW_SECONDS", ge=1)
    bsc_ws_failback_probe_seconds: float = Field(30, alias="BSC_WS_FAILBACK_PROBE_SECONDS", gt=0)
    bsc_ws_failback_required_successes: int = Field(
        3,
        validation_alias=AliasChoices(
            "BSC_WS_FAILBACK_HEALTHY_PROBES", "BSC_WS_FAILBACK_REQUIRED_SUCCESSES"
        ),
        serialization_alias="BSC_WS_FAILBACK_HEALTHY_PROBES",
        ge=1,
    )
    bsc_ws_handoff_buffer_max_events: int = Field(
        10_000, alias="BSC_WS_HANDOFF_BUFFER_MAX_EVENTS", ge=1
    )
    graph_max_total_nodes: int = Field(2_000, alias="GRAPH_MAX_TOTAL_NODES", ge=1)
    graph_max_wallet_nodes: int = Field(2_000, alias="GRAPH_MAX_WALLET_NODES", ge=1)
    graph_max_token_nodes: int = Field(2_000, alias="GRAPH_MAX_TOKEN_NODES", ge=1)
    graph_max_trade_nodes: int = Field(5_000, alias="GRAPH_MAX_TRADE_NODES", ge=1)
    graph_max_cluster_nodes: int = Field(500, alias="GRAPH_MAX_CLUSTER_NODES", ge=1)
    graph_max_edges: int = Field(10_000, alias="GRAPH_MAX_EDGES", ge=1)
    graph_max_source_events: int = Field(20_000, alias="GRAPH_MAX_SOURCE_EVENTS", ge=1)
    graph_max_pair_checks: int = Field(80_000, alias="GRAPH_MAX_PAIR_CHECKS", ge=1)
    market_api_key: SecretStr | None = Field(None, alias="MARKET_API_KEY")
    dex_api_key: SecretStr | None = Field(None, alias="DEX_API_KEY")
    telegram_bot_token: SecretStr | None = Field(None, alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str | None = Field(None, alias="TELEGRAM_CHAT_ID")
    trading_mode: Literal["PAPER", "LIVE"] = Field("PAPER", alias="TRADING_MODE")
    enable_live_trading: bool = Field(False, alias="ENABLE_LIVE_TRADING")
    live_confirmation: bool = Field(False, alias="LIVE_CONFIRMATION")
    trading_wallet_private_key: SecretStr | None = Field(None, alias="TRADING_WALLET_PRIVATE_KEY")
    max_position_usd: float = Field(100, alias="MAX_POSITION_USD", gt=0)
    max_daily_loss_usd: float = Field(50, alias="MAX_DAILY_LOSS_USD", gt=0)
    max_open_positions: int = Field(5, alias="MAX_OPEN_POSITIONS", ge=1)
    max_slippage_bps: int = Field(300, alias="MAX_SLIPPAGE_BPS", ge=0)
    paper_initial_balance_usd: float = Field(1000, alias="PAPER_INITIAL_BALANCE_USD", gt=0)
    early_buyer_top_n: int = Field(20, alias="EARLY_BUYER_TOP_N", ge=1)
    early_buyer_window_type: Literal["buyers", "blocks", "minutes"] = Field("buyers", alias="EARLY_BUYER_WINDOW_TYPE")
    early_buyer_window_value: int | None = Field(None, alias="EARLY_BUYER_WINDOW_VALUE", ge=1)
    early_buyer_max_entry_hours: int = Field(24, alias="EARLY_BUYER_MAX_ENTRY_HOURS", gt=0)
    # Secondary external evidence providers (never authoritative risk inputs)
    external_evidence_enabled: bool = Field(True, alias="EXTERNAL_EVIDENCE_ENABLED")
    dexscreener_enabled: bool = Field(True, alias="DEXSCREENER_ENABLED")
    dexscreener_base_url: str = Field("https://api.dexscreener.com", alias="DEXSCREENER_BASE_URL")
    dexscreener_timeout_seconds: float = Field(10, alias="DEXSCREENER_TIMEOUT_SECONDS", gt=0)
    dexscreener_api_key: SecretStr | None = Field(None, alias="DEXSCREENER_API_KEY")
    dexscreener_rate_limit_per_second: float = Field(5, alias="DEXSCREENER_RATE_LIMIT_PER_SECOND", gt=0)
    dexscreener_cache_ttl_seconds: int = Field(30, alias="DEXSCREENER_CACHE_TTL_SECONDS", ge=0)
    goplus_enabled: bool = Field(False, alias="GOPLUS_ENABLED")
    goplus_base_url: str = Field("https://api.gopluslabs.io", alias="GOPLUS_BASE_URL")
    goplus_timeout_seconds: float = Field(10, alias="GOPLUS_TIMEOUT_SECONDS", gt=0)
    goplus_api_key: SecretStr | None = Field(None, alias="GOPLUS_API_KEY")
    goplus_app_key: SecretStr | None = Field(None, alias="GOPLUS_APP_KEY")
    goplus_app_secret: SecretStr | None = Field(None, alias="GOPLUS_APP_SECRET", repr=False)
    goplus_rate_limit_per_second: float = Field(2, alias="GOPLUS_RATE_LIMIT_PER_SECOND", gt=0)
    goplus_cache_ttl_seconds: int = Field(300, alias="GOPLUS_CACHE_TTL_SECONDS", ge=0)
    external_evidence_max_backoff_seconds: float = Field(60, alias="EXTERNAL_EVIDENCE_MAX_BACKOFF_SECONDS", gt=0)

    @field_validator("trading_mode", mode="before")
    @classmethod
    def normalize_mode(cls, value: str) -> str:
        return str(value).upper()

    @field_validator("analyst_key", mode="before")
    @classmethod
    def empty_analyst_key(cls, value):
        return None if value is None or value == "" else value

    @field_validator("analyst_key")
    @classmethod
    def validate_analyst_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", value.get_secret_value()):
            raise ValueError("SMMS_ANALYST_KEY must contain32..256 URL-safe ASCII characters")
        return value

    @model_validator(mode="after")
    def enforce_live_safety(self) -> "Settings":
        if self.database_host is not None:
            if not self.database_host.strip() or self.database_password is None or not self.database_password.get_secret_value():
                raise ValueError("Structured database configuration requires a host and password")
            self.database_url = URL.create("postgresql+asyncpg", username=self.database_user,
                password=self.database_password.get_secret_value(), host=self.database_host,
                # Driver connections need credentials; this field is excluded from repr/dumps.
                port=self.database_port, database=self.database_name).render_as_string(False)
        if self.bsc_chain_id != BSC_CHAIN_ID:
            raise ValueError(f"BSC_CHAIN_ID must be {BSC_CHAIN_ID}")
        if self.trading_mode == "LIVE" and not (self.enable_live_trading and self.live_confirmation):
            raise ValueError("LIVE mode requires ENABLE_LIVE_TRADING=true and LIVE_CONFIRMATION=true")
        if self.enable_live_trading and not self.live_confirmation:
            raise ValueError("ENABLE_LIVE_TRADING=true requires LIVE_CONFIRMATION=true")
        return self

    @property
    def is_paper_mode(self) -> bool:
        return self.trading_mode == "PAPER"

    @property
    def bsc_rpc_endpoints(self) -> tuple[str, ...]:
        """Return configured BSC endpoints without exposing credentials."""

        return _split_endpoints(self.bsc_rpc_urls, self.bsc_rpc_url)

    @property
    def bsc_ws_endpoints(self) -> tuple[str, ...]:
        return _split_endpoints(self.bsc_ws_urls, self.bsc_ws_url)

    @property
    def goplus_auth_configured(self) -> bool:
        """Whether the documented app-key/app-secret flow can be used."""

        return all(
            value is not None and bool(value.get_secret_value().strip())
            for value in (self.goplus_app_key, self.goplus_app_secret)
        )

    @property
    def bsc_provider_name(self) -> str:
        if self.bsc_ws_endpoints:
            return "bsc_ws"
        return "bsc_http_polling" if self.bsc_rpc_endpoints else "not_configured"

    @property
    def production_chain(self) -> str:
        return DEFAULT_CHAIN


@lru_cache
def get_settings() -> Settings:
    return Settings()


def _split_endpoints(*values: str | None) -> tuple[str, ...]:
    """Parse a preferred comma-separated list with deterministic precedence."""

    selected = next((value for value in values if value is not None and value.strip()), "")
    return tuple(dict.fromkeys(item.strip() for item in selected.split(",") if item.strip()))


def get_bsc_rpc_urls(settings: Settings | None = None) -> tuple[str, ...]:
    """Return BSC HTTP endpoints, preferring ``BSC_RPC_URLS`` over ``BSC_RPC_URL``."""

    return (settings or get_settings()).bsc_rpc_endpoints


def get_bsc_ws_urls(settings: Settings | None = None) -> tuple[str, ...]:
    """Return BSC WebSocket endpoints, preferring ``BSC_WS_URLS`` over ``BSC_WS_URL``."""

    return (settings or get_settings()).bsc_ws_endpoints

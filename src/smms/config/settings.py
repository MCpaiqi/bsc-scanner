from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-backed application settings with paper-first safety defaults."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = Field("development", alias="APP_ENV")
    log_level: str = Field("INFO", alias="LOG_LEVEL")
    database_url: str = Field(
        "postgresql+asyncpg://postgres:postgres@localhost:5432/smms", alias="DATABASE_URL"
    )
    redis_url: str = Field("redis://localhost:6379/0", alias="REDIS_URL")
    solana_rpc_url: str | None = Field(None, alias="SOLANA_RPC_URL")
    solana_ws_url: str | None = Field(None, alias="SOLANA_WS_URL")
    bsc_rpc_url: str | None = Field(None, alias="BSC_RPC_URL")
    bsc_rpc_urls: str | None = Field(None, alias="BSC_RPC_URLS")
    bsc_ws_url: str | None = Field(None, alias="BSC_WS_URL")
    bsc_chain_id: int = Field(56, alias="BSC_CHAIN_ID", ge=1)
    bsc_rpc_timeout_seconds: float = Field(10, alias="BSC_RPC_TIMEOUT_SECONDS", gt=0)
    bsc_rpc_max_retries: int = Field(2, alias="BSC_RPC_MAX_RETRIES", ge=0)
    bsc_rpc_retry_backoff_seconds: float = Field(0.25, alias="BSC_RPC_RETRY_BACKOFF_SECONDS", ge=0)
    bsc_log_chunk_size: int = Field(10_000, alias="BSC_LOG_CHUNK_SIZE", gt=0)
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

    @field_validator("trading_mode", mode="before")
    @classmethod
    def normalize_mode(cls, value: str) -> str:
        return str(value).upper()

    @model_validator(mode="after")
    def enforce_live_safety(self) -> "Settings":
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

        values = self.bsc_rpc_urls or self.bsc_rpc_url or ""
        return tuple(value.strip() for value in values.split(",") if value.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()

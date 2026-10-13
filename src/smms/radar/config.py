"""Environment-configurable radar ranking and bounded freshness policy."""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class RadarConfig(BaseModel):
    model_config = ConfigDict(validate_default=True, extra="forbid", frozen=True)
    recency_weight: Decimal = Field(20, ge=0)
    buy_pressure_weight: Decimal = Field(20, ge=0)
    smart_money_weight: Decimal = Field(40, ge=0)
    liquidity_weight: Decimal = Field(20, ge=0)
    high_risk_penalty: Decimal = Field(30, ge=0)
    medium_risk_penalty: Decimal = Field(10, ge=0)
    liquidity_target_usd: Decimal = Field(100000, gt=0)
    recency_seconds: int = Field(3600, gt=0)
    stale_seconds: int = Field(3600, gt=0)
    snapshot_max_age_seconds: int = Field(3600, gt=0)
    discovery_lookback_seconds: int = Field(86400, gt=0)
    min_trade_usd: Decimal = Field(10, ge=0)
    # Used only when USD is unavailable; native units, never raw token units.
    min_trade_native: Decimal = Field(Decimal("0.01"), gt=0)
    score_version: str = "v2"
    risk_version: str = "risk-v1"
    signal_version: str = "signal-v1"

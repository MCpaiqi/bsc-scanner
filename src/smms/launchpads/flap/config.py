"""Configuration for the Flap launchpad adapter.

The adapter deliberately keeps contract addresses, event signatures, and ABI
field positions outside of code.  Flap's on-chain details must be verified by
the caller before a configuration is enabled.
"""

from collections.abc import Mapping
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_index(value: Any, *, name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise TypeError(f"{name} must be a non-negative integer")
    try:
        index = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a non-negative integer") from exc
    if index < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return index


@dataclass(frozen=True)
class FlapConfig:
    """Explicit selectors and scaling used by the Flap adapter.

    ``None`` means that a value is not verified/configured.  No contract
    address, event topic, curve parameter, or graduation threshold is
    fabricated here.
    """

    chain: str = "bsc"
    launchpad: str = "FLAP"
    enabled: bool = True
    source: str = "bsc_rpc:flap"

    factory_address: str | None = None
    portal_address: str | None = None
    router_address: str | None = None
    bonding_curve_address: str | None = None
    creation_contract_address: str | None = None
    creation_event_topic: str | None = None
    creation_token_topic_index: int | None = None
    creation_creator_topic_index: int | None = None
    creation_token_data_index: int | None = None
    creation_creator_data_index: int | None = None

    trade_contract_address: str | None = None
    trade_event_topic: str | None = None
    buy_event_topic: str | None = None
    sell_event_topic: str | None = None
    trade_trader_topic_index: int | None = None
    trade_token_topic_index: int | None = None
    trade_trader_data_index: int | None = None
    trade_token_data_index: int | None = None
    trade_token_amount_topic_index: int | None = None
    trade_native_amount_topic_index: int | None = None
    trade_token_amount_data_index: int | None = None
    trade_native_amount_data_index: int | None = None
    trade_price_data_index: int | None = None
    trade_market_cap_data_index: int | None = None
    trade_liquidity_data_index: int | None = None
    trade_side_data_index: int | None = None
    trade_side_topic_index: int | None = None
    trade_buy_side_value: int | None = None
    trade_sell_side_value: int | None = None
    default_trade_side: str | None = None

    token_decimals: int | None = None
    native_decimals: int | None = None
    price_decimals: int | None = None
    market_cap_decimals: int | None = None
    liquidity_decimals: int | None = None

    lifecycle_contract_address: str | None = None
    trading_started_event_topic: str | None = None
    near_graduation_event_topic: str | None = None
    graduation_event_topic: str | None = None
    dex_listed_event_topic: str | None = None
    lifecycle_token_topic_index: int | None = None
    lifecycle_token_data_index: int | None = None

    def __post_init__(self) -> None:
        chain = _optional_text(self.chain)
        launchpad = _optional_text(self.launchpad)
        source = _optional_text(self.source)
        if not chain:
            raise ValueError("chain is required")
        if not launchpad:
            raise ValueError("launchpad is required")
        if not source:
            raise ValueError("source is required")
        object.__setattr__(self, "chain", chain.lower())
        object.__setattr__(self, "launchpad", launchpad.upper())
        object.__setattr__(self, "source", source)

        text_fields = {
            field.name
            for field in fields(self)
            if field.type in {str | None, str} or field.name.endswith("_address")
        }
        for name in text_fields:
            if name in {"chain", "launchpad", "source"}:
                continue
            value = _optional_text(getattr(self, name))
            object.__setattr__(self, name, value)

        index_fields = {
            field.name
            for field in fields(self)
            if "index" in field.name
        }
        for name in index_fields:
            object.__setattr__(self, name, _optional_index(getattr(self, name), name=name))

        for name in (
            "price_decimals",
            "market_cap_decimals",
            "liquidity_decimals",
        ):
            value = getattr(self, name)
            if value is None:
                continue
            if isinstance(value, bool):
                raise TypeError(f"{name} must be between 0 and 255")
            try:
                value = int(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{name} must be between 0 and 255") from exc
            if not 0 <= value <= 255:
                raise ValueError(f"{name} must be between 0 and 255")
            object.__setattr__(self, name, value)

        for name in ("trade_buy_side_value", "trade_sell_side_value"):
            value = getattr(self, name)
            if value is not None:
                if isinstance(value, bool):
                    raise ValueError(f"{name} must be an integer")
                try:
                    object.__setattr__(self, name, int(value))
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"{name} must be an integer") from exc

        if self.creation_contract_address and not self.factory_address:
            object.__setattr__(self, "factory_address", self.creation_contract_address)
        elif self.factory_address and not self.creation_contract_address:
            object.__setattr__(self, "creation_contract_address", self.factory_address)

        if self.bonding_curve_address and not self.trade_contract_address:
            object.__setattr__(self, "trade_contract_address", self.bonding_curve_address)
        elif self.trade_contract_address and not self.bonding_curve_address:
            object.__setattr__(self, "bonding_curve_address", self.trade_contract_address)

        if self.default_trade_side is not None:
            side = self.default_trade_side.upper()
            if side not in {"BUY", "SELL"}:
                raise ValueError("default_trade_side must be BUY or SELL")
            object.__setattr__(self, "default_trade_side", side)

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any]) -> "FlapConfig":
        """Build a config from a full YAML mapping or a Flap subsection."""

        root: Mapping[str, Any] = mapping
        launchpads = root.get("launchpads")
        if isinstance(launchpads, Mapping):
            flap = launchpads.get("flap")
            if isinstance(flap, Mapping):
                root = flap
        elif isinstance(root.get("flap"), Mapping):
            root = root["flap"]

        values: dict[str, Any] = {}
        valid_names = {field.name for field in fields(cls)}
        for name in valid_names:
            if name in root:
                values[name] = root[name]
        return cls(**values)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "FlapConfig":
        """Load a Flap config without assigning values absent from YAML."""

        try:
            import yaml
        except ImportError as exc:  # pragma: no cover - dependency is project standard
            raise RuntimeError("PyYAML is required to load Flap configuration") from exc
        with Path(path).open("r", encoding="utf-8") as stream:
            parsed = yaml.safe_load(stream) or {}
        if not isinstance(parsed, Mapping):
            raise TypeError("Flap configuration must be a mapping")
        return cls.from_mapping(parsed)


__all__ = ["FlapConfig"]

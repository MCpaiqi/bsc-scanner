from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from smms.chains.constants import DEFAULT_CHAIN
from smms.ingestion.types import RawSwap, RawToken, RawTransaction, utc_from_unix


class NormalizationError(ValueError):
    """Raised when required source fields cannot be normalized authoritatively."""


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise NormalizationError(f"Invalid numeric value: {value!r}") from exc


def _normalized_amount(raw: Any, decimals: Any) -> Decimal | None:
    value = _decimal(raw)
    if value is None:
        return None
    try:
        exponent = int(decimals or 0)
    except (TypeError, ValueError) as exc:
        raise NormalizationError(f"Invalid decimals: {decimals!r}") from exc
    if exponent < 0 or exponent > 255:
        raise NormalizationError(f"Invalid decimals: {exponent}")
    return value / (Decimal(10) ** exponent)


def normalize_token(raw: RawToken) -> dict[str, Any]:
    address = raw.get("address")
    if not address:
        raise NormalizationError("Token address is required")
    return {
        "address": address,
        "chain": raw.get("chain", DEFAULT_CHAIN),
        "symbol": raw.get("symbol"),
        "name": raw.get("name"),
        "metadata_json": {**raw.get("metadata", {}), "decimals": raw.get("decimals")},
    }


def normalize_transaction(raw: RawTransaction) -> dict[str, Any]:
    signature = raw.get("signature")
    wallet_address = raw.get("wallet_address")
    if not signature or not wallet_address:
        raise NormalizationError("Transaction signature and wallet address are required")
    timestamp = utc_from_unix(raw.get("block_time"))
    if timestamp is None:
        raise NormalizationError("Transaction block_time is unavailable")
    return {
        "signature": signature,
        "wallet_address": wallet_address,
        "timestamp": timestamp,
        "slot": raw.get("slot"),
        "token_in": raw.get("token_in"),
        "token_out": raw.get("token_out"),
        "amount_in_raw": _decimal(raw.get("amount_in_raw")),
        "amount_out_raw": _decimal(raw.get("amount_out_raw")),
        "amount_in": _normalized_amount(raw.get("amount_in_raw"), raw.get("decimals_in")),
        "amount_out": _normalized_amount(raw.get("amount_out_raw"), raw.get("decimals_out")),
        "side": raw.get("side", "UNKNOWN"),
        "dex": raw.get("dex"),
        "metadata_json": raw.get("metadata", {}),
    }


def normalize_swap(raw: RawSwap) -> dict[str, Any]:
    signature = raw.get("signature")
    wallet_address = raw.get("wallet_address")
    if not signature or not wallet_address:
        raise NormalizationError("Swap signature and wallet address are required")
    timestamp_value = raw.get("timestamp")
    if isinstance(timestamp_value, datetime):
        timestamp = timestamp_value.astimezone(UTC)
    else:
        timestamp = utc_from_unix(timestamp_value)
    if timestamp is None:
        raise NormalizationError("Swap timestamp is unavailable")
    return {
        "signature": signature,
        "wallet_address": wallet_address,
        "timestamp": timestamp,
        "token_in": raw.get("token_in"),
        "token_out": raw.get("token_out"),
        "amount_in_raw": _decimal(raw.get("amount_in_raw")),
        "amount_out_raw": _decimal(raw.get("amount_out_raw")),
        "amount_in": _normalized_amount(raw.get("amount_in_raw"), raw.get("decimals_in")),
        "amount_out": _normalized_amount(raw.get("amount_out_raw"), raw.get("decimals_out")),
        "usd_value": _decimal(raw.get("usd_value")),
        "dex": raw.get("dex"),
        "metadata_json": raw.get("metadata", {}),
    }

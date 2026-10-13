"""Shared chain and address vocabulary for the production BSC lane.

The database still accepts historical chain provenance, but the active runtime
has one supported chain.  ``normalize_chain`` keeps an explicit legacy escape
hatch for old data/repository readers; HTTP/runtime callers use
``validate_bsc_chain`` and therefore cannot silently convert another chain.
"""

from __future__ import annotations

import re
from typing import Final

DEFAULT_CHAIN = "bsc"
BSC_CHAIN_ID: Final[int] = 56
SUPPORTED_CHAINS = frozenset({DEFAULT_CHAIN})
LEGACY_CHAINS = frozenset({"solana"})
BSC_ADDRESS_PATTERN: Final[re.Pattern[str]] = re.compile(r"^0x[0-9a-fA-F]{40}$")


def normalize_chain(
    value: str | None,
    *,
    default: str = DEFAULT_CHAIN,
    allow_legacy: bool = True,
) -> str:
    """Normalize a chain name without silently falling back.

    ``allow_legacy`` exists only for historical database/replay readers.  New
    production paths must leave it false (or call ``validate_bsc_chain``).
    """

    chain = (value or default).strip().lower()
    allowed_chains = SUPPORTED_CHAINS | (LEGACY_CHAINS if allow_legacy else frozenset())
    if chain not in allowed_chains:
        allowed = ", ".join(sorted(SUPPORTED_CHAINS))
        raise ValueError(f"chain must be one of {allowed}")
    return chain


def validate_bsc_chain(value: str | None) -> str:
    """Return ``bsc`` or raise for every non-production chain value."""

    try:
        chain = normalize_chain(value, allow_legacy=False)
    except ValueError as exc:
        raise ValueError("当前系统仅支持 BNB Smart Chain（BSC）。") from exc
    if chain != DEFAULT_CHAIN:
        raise ValueError("当前系统仅支持 BNB Smart Chain（BSC）。")
    return chain


def validate_bsc_address(value: str) -> bool:
    """Whether *value* is a 20-byte, hexadecimal BSC/EVM address."""

    return isinstance(value, str) and bool(BSC_ADDRESS_PATTERN.fullmatch(value.strip()))


def normalize_bsc_address(value: str) -> str:
    """Validate and canonicalize a BSC address at an input seam."""

    address = str(value).strip()
    if not validate_bsc_address(address):
        raise ValueError("BSC 地址必须是以 0x 开头的 20-byte 十六进制地址。")
    return address.lower()


def normalize_address(value: str, *, chain: str | None = None, strict: bool = False) -> str:
    """Return the canonical identity form used by persistence and queries."""

    address = str(value).strip()
    if (chain or "").strip().lower() == DEFAULT_CHAIN:
        # Persistence/replay code may still carry synthetic historical fixtures;
        # strict validation belongs at external BSC input seams.
        if strict:
            return normalize_bsc_address(address)
        return address.lower()
    return address


__all__ = [
    "BSC_ADDRESS_PATTERN",
    "BSC_CHAIN_ID",
    "DEFAULT_CHAIN",
    "LEGACY_CHAINS",
    "SUPPORTED_CHAINS",
    "normalize_address",
    "normalize_bsc_address",
    "normalize_chain",
    "validate_bsc_address",
    "validate_bsc_chain",
]

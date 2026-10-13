"""Bounded ERC-20 metadata enrichment for discovered BSC tokens."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from Crypto.Hash import keccak
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .identity import PLACEHOLDER_TEXTS, display_text


def _metadata_status(symbol: str | None, name: str | None, decimals: int | None) -> str:
    present = (symbol is not None, name is not None, decimals is not None)
    return "KNOWN" if all(present) else "PARTIAL" if any(present) else "UNKNOWN"


def _selector(signature: str) -> str:
    digest = keccak.new(digest_bits=256)
    digest.update(signature.encode())
    return "0x" + digest.hexdigest()[:8]


def _decode_uint256(value: str) -> int | None:
    try:
        payload = value.removeprefix("0x")
        return int(payload[-64:], 16) if len(payload) >= 64 else None
    except (TypeError, ValueError):
        return None


def _decode_string(value: str) -> str | None:
    try:
        payload = value.removeprefix("0x")
        if len(payload) == 64:
            return bytes.fromhex(payload).rstrip(b"\x00").decode("utf-8", errors="replace").strip() or None
        if len(payload) < 128:
            return None
        offset = int(payload[:64], 16) * 2
        length = int(payload[offset : offset + 64], 16)
        raw = payload[offset + 64 : offset + 64 + length * 2]
        return bytes.fromhex(raw).decode("utf-8", errors="replace").strip() or None
    except (TypeError, ValueError, UnicodeError):
        return None


async def enrich_erc20_metadata(
    provider: Any,
    address: str,
    *,
    timeout_seconds: float = 3.0,
    retries: int = 1,
    cache: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Read symbol/name/decimals with timeout and fail-closed status."""
    key = address.lower()
    if cache is not None and key in cache:
        return dict(cache[key])

    async def call(signature: str) -> str | None:
        for attempt in range(max(0, retries) + 1):
            try:
                return await asyncio.wait_for(
                    provider.eth_call(address, _selector(signature)), timeout=timeout_seconds
                )
            except Exception:  # noqa: BLE001 - metadata must never block discovery
                if attempt >= retries:
                    return None
                await asyncio.sleep(0)
        return None

    symbol_raw, name_raw, decimals_raw = await asyncio.gather(
        call("symbol()"), call("name()"), call("decimals()")
    )
    decimals = _decode_uint256(decimals_raw) if decimals_raw else None
    if decimals is not None and not 0 <= decimals <= 255:
        decimals = None
    symbol = display_text(_decode_string(symbol_raw), address) if symbol_raw else None
    name = display_text(_decode_string(name_raw), address) if name_raw else None
    status = _metadata_status(symbol, name, decimals)
    reason = None
    if status == "PARTIAL":
        reason = "METADATA_PARTIAL"
    elif status == "UNKNOWN":
        reason = (
            "METADATA_PROVIDER_TIMEOUT"
            if all(raw is None for raw in (symbol_raw, name_raw, decimals_raw))
            else "METADATA_EMPTY_RESPONSE"
        )
    result = {
        "symbol": symbol,
        "name": name,
        "decimals": decimals,
        "metadata_status": status,
        "metadata_reason_code": reason,
    }
    if cache is not None:
        cache[key] = dict(result)
    return result


async def backfill_missing_erc20_metadata(
    session: AsyncSession,
    provider: Any,
    *,
    batch_size: int = 25,
    rate_limit_per_second: float = 5.0,
    retries: int = 2,
) -> dict[str, int]:
    """Retry missing token metadata and persist auditable failure reasons."""
    from smms.db.models import Token

    def missing_text(column):
        normalized = func.lower(func.trim(column, " \t\r\n"))
        return (
            column.is_(None)
            | normalized.in_(["", *sorted(PLACEHOLDER_TEXTS)])
            | (normalized == func.lower(Token.address))
        )

    rows = (await session.scalars(
        select(Token).where(
            Token.chain == "bsc",
            (missing_text(Token.symbol) | missing_text(Token.name) | Token.decimals.is_(None)),
        ).order_by(
            # A permanently unreadable token must not monopolize every batch.
            Token.metadata_json["metadata_backfill_last_attempt_at"].as_string().asc().nullsfirst(),
            # Among first attempts, current discoveries take precedence over
            # a historical backlog so freshly scanned names appear promptly.
            Token.id.desc(),
        ).limit(max(1, int(batch_size)))
    )).all()
    interval = 1 / rate_limit_per_second if rate_limit_per_second > 0 else 0
    stats = {"attempted": 0, "updated": 0, "failed": 0}
    last_call = 0.0
    loop = asyncio.get_running_loop()
    for token in rows:
        wait = interval - (loop.time() - last_call)
        if wait > 0:
            await asyncio.sleep(wait)
        last_call = loop.time()
        stats["attempted"] += 1
        metadata = await enrich_erc20_metadata(provider, str(token.address).lower(), retries=retries)
        existing = dict(token.metadata_json or {})
        existing.update({
            "metadata_backfill_attempts": int(existing.get("metadata_backfill_attempts", 0) or 0) + 1,
            "metadata_backfill_last_attempt_at": datetime.now(UTC).isoformat(),
        })
        changed = False
        for field, limit in (("symbol", 32), ("name", 128)):
            value = display_text(metadata.get(field), token.address)
            if display_text(getattr(token, field), token.address) is None and value is not None:
                setattr(token, field, value[:limit])
                existing[field] = value
                changed = True
        if token.decimals is None and metadata.get("decimals") is not None:
            token.decimals = int(metadata["decimals"])
            changed = True
        status = _metadata_status(
            display_text(token.symbol, token.address),
            display_text(token.name, token.address),
            token.decimals,
        )
        existing["metadata_status"] = status
        existing["metadata_backfill_reason_code"] = (
            None if status == "KNOWN" else metadata.get("metadata_reason_code")
        )
        token.metadata_json = existing
        stats["updated"] += int(changed)
        stats["failed"] += int(status != "KNOWN")
    await session.flush()
    return stats


__all__ = ["backfill_missing_erc20_metadata", "enrich_erc20_metadata"]

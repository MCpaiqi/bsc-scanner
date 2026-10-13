"""Market snapshot materialization from canonical trades and provider observations."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from smms.db.models import TokenMarketSnapshot, TradeEvent
from smms.observability import metrics


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value)) if value is not None else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _side(value: Any) -> str:
    return str(getattr(value, "value", value)).upper()


def _aggregate(rows: list[TradeEvent], *, as_of: datetime) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for minutes in (1, 5):
        start = as_of - timedelta(minutes=minutes)
        window = [row for row in rows if start < row.timestamp <= as_of and row.canonical and not row.removed]
        buys = [row for row in window if _side(row.side) == "BUY"]
        sells = [row for row in window if _side(row.side) == "SELL"]
        known_usd = [row.value_usd for row in window if row.value_usd is not None]
        token_values = [row.token_amount for row in window if row.token_amount is not None]
        result.update(
            {
                f"volume_{minutes}m": sum(known_usd, Decimal(0)) if known_usd else None,
                f"volume_token_{minutes}m": sum(token_values, Decimal(0)) if len(token_values) == len(window) and window else None,
                f"buys_{minutes}m": len(buys) if window else None,
                f"sells_{minutes}m": len(sells) if window else None,
                f"unique_buyers_{minutes}m": len({row.trader_address for row in buys}) if buys else (0 if window else None),
            }
        )
    return result


async def materialize_trade_snapshot(
    session: AsyncSession,
    trade: TradeEvent,
    *,
    as_of: datetime | None = None,
) -> TokenMarketSnapshot:
    cutoff = as_of or trade.timestamp or datetime.now(UTC)
    rows = list(
        (
            await session.scalars(
                select(TradeEvent).where(
                    TradeEvent.chain == trade.chain,
                    TradeEvent.token_address == trade.token_address,
                    TradeEvent.timestamp > cutoff - timedelta(minutes=5),
                    TradeEvent.timestamp <= cutoff,
                )
            )
        ).all()
    )
    values = _aggregate(rows, as_of=cutoff)
    identity = {
        "chain": trade.chain,
        "token_address": trade.token_address,
        "pair_address": trade.pair_address or "UNKNOWN",
        "source": "ON_CHAIN_TRADE_AGGREGATION",
        "as_of": cutoff,
    }
    snapshot = await session.scalar(
        select(TokenMarketSnapshot).where(
            TokenMarketSnapshot.chain == identity["chain"],
            TokenMarketSnapshot.token_address == identity["token_address"],
            TokenMarketSnapshot.pair_address == identity["pair_address"],
            TokenMarketSnapshot.source == identity["source"],
            TokenMarketSnapshot.as_of == identity["as_of"],
        )
    )
    snapshot_values = {
        "chain_id": trade.chain_id or 56,
        "dex": (trade.metadata_json or {}).get("dex"),
        "observed_at": cutoff,
        "freshness_seconds": 0,
        "status": "KNOWN",
        "metadata_json": {
            "price_status": "UNKNOWN" if trade.price_usd is None else "KNOWN",
            "volume_usd_coverage": {
                "known": sum(1 for row in rows if row.value_usd is not None),
                "total": len(rows),
            },
        },
        **values,
    }
    if snapshot is None:
        snapshot = TokenMarketSnapshot(**identity, **snapshot_values)
        session.add(snapshot)
    else:
        for key, value in snapshot_values.items():
            setattr(snapshot, key, value)
    await session.flush()
    metrics.increment("market_snapshots_updated")
    return snapshot


def materialize_dexscreener_snapshot(
    session: Session,
    payload: dict[str, Any],
    *,
    as_of: datetime | None = None,
) -> TokenMarketSnapshot:
    """Persist normalized DexScreener data without relabeling it as on-chain."""
    observed = as_of or datetime.now(UTC)
    txns = payload.get("txns_m5") if isinstance(payload.get("txns_m5"), dict) else {}
    snapshot = TokenMarketSnapshot(
        chain="bsc",
        chain_id=56,
        token_address=str(payload["token_address"]).lower(),
        pair_address=str(payload.get("pair_address") or "UNKNOWN").lower(),
        dex=payload.get("dex_id"),
        price_usd=_decimal(payload.get("price_usd")),
        market_cap_usd=_decimal(payload.get("market_cap_usd")),
        fdv_usd=_decimal(payload.get("fdv_usd")),
        liquidity_usd=_decimal(payload.get("liquidity_usd")),
        volume_5m=_decimal(payload.get("volume_m5")),
        volume_h1=_decimal(payload.get("volume_h1")),
        volume_h24=_decimal(payload.get("volume_h24")),
        buys_5m=int(txns["buys"]) if txns.get("buys") is not None else None,
        sells_5m=int(txns["sells"]) if txns.get("sells") is not None else None,
        price_change_5m=_decimal(payload.get("price_change_m5")),
        price_change_h1=_decimal(payload.get("price_change_h1")),
        source="DEXSCREENER",
        observed_at=observed,
        as_of=observed,
        freshness_seconds=0,
        status="KNOWN",
        metadata_json={"provider_provenance": "DexScreener API",
                       **{key: payload.get(key) for key in ("symbol", "name", "image_url", "dex_url", "quote_symbol")}},
    )
    session.add(snapshot)
    session.flush()
    metrics.increment("market_snapshots_updated")
    return snapshot


__all__ = ["materialize_dexscreener_snapshot", "materialize_trade_snapshot"]

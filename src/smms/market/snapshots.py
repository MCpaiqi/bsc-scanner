"""Market snapshot materialization from canonical trades and provider observations."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from smms.db.facts import canonical_fact_conditions
from smms.db.models import TokenMarketSnapshot, TradeEvent
from smms.evaluation import utc
from smms.observability import metrics
from smms.research.lineage import manifest, row_values
from smms.research.publication import publish_snapshot
from smms.research.state import capture_epoch


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
        window = [row for row in rows if start < utc(row.timestamp) <= as_of and row.canonical and not row.removed]
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


async def materialize_trade_snapshot(session: AsyncSession, trade: TradeEvent, *,
                                     as_of: datetime | None = None) -> TokenMarketSnapshot | None:
    return await session.run_sync(lambda sync: rebuild_trade_snapshot(sync, trade, as_of=as_of))


def rebuild_trade_snapshot(session: Session, trade: TradeEvent, *, as_of: datetime | None = None,
                           token_address: str | None = None, pair_address: str | None = None,
                           source_epoch: int | None = None):
    cutoff = utc(as_of or trade.timestamp or datetime.now(UTC))
    epoch = capture_epoch(session, trade.chain) if source_epoch is None else source_epoch
    token_address = token_address or trade.token_address
    pair_address = pair_address or trade.pair_address or "UNKNOWN"
    rows = list(session.scalars(select(TradeEvent).where(
        TradeEvent.chain == trade.chain, TradeEvent.token_address == token_address,
        TradeEvent.pair_address == pair_address,
        TradeEvent.timestamp > cutoff - timedelta(minutes=5), TradeEvent.timestamp <= cutoff,
        *canonical_fact_conditions(TradeEvent),
    ).order_by(TradeEvent.timestamp, TradeEvent.id)))
    if not rows:
        return None
    values = _aggregate(rows, as_of=cutoff)
    known = sum(1 for row in rows if row.value_usd is not None)
    record = TokenMarketSnapshot(
        chain=trade.chain, token_address=token_address, pair_address=pair_address,
        source="ON_CHAIN_TRADE_AGGREGATION", as_of=cutoff, chain_id=trade.chain_id or 56,
        dex=(trade.metadata_json or {}).get("dex"), observed_at=cutoff, freshness_seconds=0,
        status="KNOWN" if known == len(rows) else "DEGRADED", source_epoch=epoch,
        input_manifest=manifest(kind="token_market_snapshots", parameters={
            "token_address": token_address, "pair_address": pair_address,
            "as_of": cutoff, "source": "ON_CHAIN_TRADE_AGGREGATION", "window_seconds": 300,
            "input_policy": "canonical-research-v1",
        }, facts={"trades": [row_values(row) for row in rows]}),
        metadata_json={"price_status": "UNKNOWN", "volume_usd_coverage": {"known": known, "total": len(rows)}},
        **values,
    )
    result = publish_snapshot(session, record)
    metrics.increment("market_snapshots_updated")
    return result


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
        source_epoch=-1, input_manifest=manifest(kind="token_market_snapshots",
            parameters={"provider": "DEXSCREENER", "as_of": observed}, facts={"provider_observation": payload}),
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
    result = publish_snapshot(session, snapshot)
    metrics.increment("market_snapshots_updated")
    return result


__all__ = ["materialize_dexscreener_snapshot", "materialize_trade_snapshot"]

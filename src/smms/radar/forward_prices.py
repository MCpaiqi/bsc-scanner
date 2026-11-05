"""Read explicit USD evidence, preserving pair identity and event-time cutoffs."""

from collections import defaultdict
from datetime import datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from smms.db.facts import canonical_fact_conditions
from smms.db.models import MarketEvent, Pair, TokenMarketSnapshot, TradeEvent
from smms.evaluation import PricePoint, positive_price, utc


def load_usd_prices(
    session: Session,
    ranges: list[tuple[str, datetime, datetime]],
    *,
    as_of: datetime,
) -> dict[str, list[PricePoint]]:
    """Batch-read exact token/time ranges, never generic unlabelled quote prices."""
    result = defaultdict(list)
    if not ranges:
        return result
    intervals = defaultdict(list)
    for token, start, end in ranges:
        intervals[token].append((utc(start), min(utc(end), utc(as_of))))
    merged = []
    for token, windows in intervals.items():
        previous = None
        for start, end in sorted(windows):
            if previous is not None and start <= previous[1]:
                previous = (previous[0], max(previous[1], end))
            else:
                if previous is not None:
                    merged.append((token, *previous))
                previous = (start, end)
        if previous is not None:
            merged.append((token, *previous))
    for priority, model, price_column, time_column in (
        (0, TradeEvent, TradeEvent.price_usd, TradeEvent.timestamp),
        (1, TokenMarketSnapshot, TokenMarketSnapshot.price_usd, TokenMarketSnapshot.observed_at),
        (2, MarketEvent, MarketEvent.price, MarketEvent.timestamp),
    ):
        chain = model.chain_id == 56 if model is MarketEvent else model.chain == "bsc"
        valid_pair = select(Pair.id).where(
            Pair.chain_id == 56, Pair.address == model.pair_address,
            Pair.creation_timestamp <= time_column,
            or_(Pair.token0_address == model.token_address, Pair.token1_address == model.token_address),
            *canonical_fact_conditions(Pair),
        ).exists()
        query = select(model).where(
            chain, price_column.is_not(None), price_column > 0, valid_pair,
            time_column <= as_of, *canonical_fact_conditions(model),
            or_(*(and_(model.token_address == token, time_column >= start, time_column <= end)
                  for token, start, end in merged)),
        )
        if model is TokenMarketSnapshot:
            query = query.where(model.status == "KNOWN", model.as_of <= as_of)
        if model is MarketEvent:
            query = query.where(model.metadata_json["price_basis"].as_string() == "USD")
        for row in session.scalars(query.order_by(time_column, model.id)):
            value = positive_price(getattr(row, price_column.key))
            if value is None:
                continue
            timestamp = utc(getattr(row, time_column.key))
            provenance = {
                "model": model.__name__, "id": row.id, "source": row.source,
                "pair_address": row.pair_address, "price_basis": "USD",
                "observed_at": timestamp.isoformat(), "price_exact": str(value),
            }
            if model is TokenMarketSnapshot:
                provenance.update(
                    as_of=utc(row.as_of).isoformat(),
                    liquidity_usd=str(row.liquidity_usd) if row.liquidity_usd is not None else None,
                    market_cap_usd=str(row.market_cap_usd) if row.market_cap_usd is not None else None,
                )
            result[row.token_address].append(PricePoint(
                timestamp, value, f"{priority}:{model.__name__}:{row.id:020d}", provenance,
            ))
    return result

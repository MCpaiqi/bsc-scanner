"""Read explicit USD evidence, preserving pair identity and event-time cutoffs."""

from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from smms.db.facts import canonical_fact_conditions
from smms.db.models import MarketEvent, Pair, TokenMarketSnapshot, TradeEvent
from smms.evaluation import PricePoint, positive_price, utc
from smms.research.lineage import row_values

ENTRY_MAX_AGE = timedelta(seconds=30)


def select_entry_price(prices, *, at):
    """Use one deterministic, already observable USD entry for both researchers."""
    at = utc(at)
    entries = [p for p in prices if at - ENTRY_MAX_AGE <= p.timestamp <= at
               and (not (p.provenance or {}).get("as_of")
                    or utc(datetime.fromisoformat(p.provenance["as_of"])) <= at)]
    latest = max((p.timestamp for p in entries), default=None)
    return min((p for p in entries if p.timestamp == latest), key=lambda p: p.reference, default=None)


def _price_rows(session, query, model, time_column, ranges):
    # Bound SQL expression depth/parameters as well as the number of due jobs.
    for offset in range(0, len(ranges), 200):
        chunk = ranges[offset:offset + 200]
        bounded = query.where(or_(*(and_(model.token_address == token, time_column >= start, time_column <= end)
                                    for token, start, end in chunk)))
        yield from session.scalars(bounded.order_by(time_column, model.id).execution_options(populate_existing=True))


def load_usd_prices(
    session: Session,
    ranges: list[tuple[str, datetime, datetime]],
    *,
    as_of: datetime,
    lineage: dict | None = None,
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
        )
        if model is TokenMarketSnapshot:
            query = query.where(model.status == "KNOWN", model.as_of <= as_of)
        if model is MarketEvent:
            query = query.where(model.metadata_json["price_basis"].as_string() == "USD")
        for row in _price_rows(session, query, model, time_column, merged):
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
                    input_digest=(row.input_manifest or {}).get("digest"),
                )
            result[row.token_address].append(PricePoint(
                timestamp, value, f"{priority}:{model.__name__}:{row.id:020d}", provenance,
            ))
            if lineage is not None:
                lineage.setdefault("sources", {})[f"{model.__name__}:{row.id}"] = {
                    "kind": model.__name__, "values": row_values(row),
                }
                if model is TokenMarketSnapshot:
                    lineage.setdefault("dependencies", {})[row.id] = {
                        "kind": row.__tablename__, "id": row.id,
                        "digest": (row.input_manifest or {}).get("digest"),
                    }
    if lineage is not None:
        addresses = {p.provenance["pair_address"] for points in result.values() for p in points}
        lineage["pairs"] = {pair.address: row_values(pair) for pair in session.scalars(select(Pair).where(
            Pair.chain_id == 56, Pair.address.in_(addresses), *canonical_fact_conditions(Pair),
        ).execution_options(populate_existing=True))} if addresses else {}
    return result


def price_facts(lineage, points):
    keys = {f"{p.provenance['model']}:{p.provenance['id']}" for p in points if p.provenance}
    pairs = {p.provenance["pair_address"] for p in points if p.provenance}
    return {"sources": [lineage["sources"][key] for key in sorted(keys)],
            "pairs": [lineage["pairs"][key] for key in sorted(pairs) if key in lineage.get("pairs", {})]}


def price_dependencies(lineage, points):
    identities = {p.provenance["id"] for p in points
                  if p.provenance and p.provenance["model"] == "TokenMarketSnapshot"}
    return [lineage["dependencies"][identity] for identity in sorted(identities)]

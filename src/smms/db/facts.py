"""SQL validity rules for canonical research facts and their reorg markers.

Pair/TradeEvent expose explicit validity columns. Market/lifecycle projections
retain reorg markers in JSON instead. A missing marker is permitted for historical
records; an explicit removed/orphaned marker is never usable research evidence.
"""

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement

from smms.db.models import MarketEvent, Pair, TokenLifecycleEvent, TokenMarketSnapshot, TradeEvent

FactModel = (type[Pair] | type[TradeEvent] | type[MarketEvent] | type[TokenLifecycleEvent]
             | type[TokenMarketSnapshot])


def canonical_fact_conditions(model: FactModel) -> tuple[ColumnElement[bool], ...]:
    """Return SQL predicates shared by historical reads and risk aggregation."""
    validity = (
        (model.canonical.is_(True), model.removed.is_(False))
        if model in (Pair, TradeEvent)
        else ()
    )
    data = model.payload_json if model is TokenLifecycleEvent else model.metadata_json
    return (
        *validity,
        data["removed"].as_boolean().is_not(True),
        data["orphaned"].as_boolean().is_not(True),
        data["confirmation_status"].as_string().is_distinct_from("orphaned"),
        *(parent_pair_conditions(model) if model in (TradeEvent, MarketEvent, TokenMarketSnapshot) else ()),
    )


def parent_pair_conditions(model):
    """Known orphan/future creation cannot be laundered through a child fact."""
    pair = aliased(Pair)
    data = pair.metadata_json
    valid = and_(pair.canonical.is_(True), pair.removed.is_(False),
                 data["removed"].as_boolean().is_not(True), data["orphaned"].as_boolean().is_not(True),
                 data["confirmation_status"].as_string().is_distinct_from("orphaned"))
    timestamp = model.as_of if model is TokenMarketSnapshot else model.timestamp
    bad = [~valid, and_(pair.creation_timestamp.is_not(None), pair.creation_timestamp > timestamp)]
    if model in (TradeEvent, MarketEvent):
        bad.append(and_(pair.creation_block.is_not(None), model.block_number.is_not(None),
                        pair.creation_block > model.block_number))
    known_invalid = select(pair.id).where(pair.chain_id == 56, pair.address == model.pair_address,
                                          or_(*bad)).exists()
    return (~known_invalid,)

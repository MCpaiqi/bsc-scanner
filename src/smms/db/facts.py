"""SQL validity rules for canonical research facts and their reorg markers.

Pair/TradeEvent expose explicit validity columns. Market/lifecycle projections
retain reorg markers in JSON instead. A missing marker is permitted for historical
records; an explicit removed/orphaned marker is never usable research evidence.
"""

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
    )

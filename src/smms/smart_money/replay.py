"""Canonical, cutoff-bound early-buyer inputs shared by research and API reads."""

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, localcontext

from sqlalchemy import or_, select

from smms.db.facts import canonical_fact_conditions
from smms.db.models import MarketEvent, Pair, TradeEvent
from smms.evaluation import utc
from smms.research.lineage import manifest, row_values
from smms.research.state import capture_epoch

from .early_buyers import EarlyBuyerConfig, identify_early_buyers

REPLAY_VERSION = "canonical-early-v1"
RADAR_ENTRY_EVENT_LIMIT = 512


class StaleReplayInputs(ValueError):
    """A fork changed while the source set was being read; retry the whole read."""


def normalized_amount(row):
    if isinstance(row, TradeEvent) or row.token_amount is None:
        return row.token_amount
    decimals = (row.metadata_json or {}).get("token_decimals")
    if isinstance(decimals, bool) or not isinstance(decimals, (int, str)):
        return None
    try:
        value = int(decimals)
        if str(value) != str(decimals) or not 0 <= value <= 255:
            return None
        with localcontext() as context:
            context.prec = 100
            return Decimal(str(row.token_amount)).scaleb(-value)
    except (ArithmeticError, TypeError, ValueError):
        return None


def usd_price(row):
    """Accounting is in USD; a generic pool quote cannot mark a USD position."""
    metadata = row.metadata_json or {}
    explicit = row.price_usd if isinstance(row, TradeEvent) else None
    if explicit is None:
        explicit = metadata.get("price_usd")
    if explicit is None and metadata.get("price_basis") == "USD":
        explicit = row.price
    value = row.value_usd if isinstance(row, TradeEvent) else row.usd_value
    try:
        amount = normalized_amount(row)
        price = Decimal(str(explicit)) if explicit is not None else (
            Decimal(str(value)) / amount if value is not None and amount and amount > 0 else None
        )
        return price if price is not None and price.is_finite() and price > 0 else None
    except (ArithmeticError, InvalidOperation, TypeError, ValueError):
        return None


@dataclass(frozen=True)
class ReplayInputs:
    chain: str
    token: str
    as_of: datetime
    source_epoch: int
    pair: dict | None
    transactions: tuple[dict, ...]
    facts: tuple[dict, ...]
    entry_only: bool = False
    source_limit: bool = False

    def cutoff(self, as_of=None):
        cutoff = utc(as_of or self.as_of)
        if cutoff > self.as_of:
            raise ValueError("replay cutoff exceeds loaded evidence")
        return cutoff

    def buyers(self, *, as_of=None, config=None):
        if self.entry_only:
            raise ValueError("entry-only evidence cannot produce position accounting")
        return self._buyers(as_of=as_of, config=config)

    def _buyers(self, *, as_of=None, config=None):
        cutoff = self.cutoff(as_of)
        if self.pair is None or utc(datetime.fromisoformat(self.pair["creation_timestamp"])) > cutoff:
            return []
        return identify_early_buyers(
            self.token, (tx for tx in self.transactions if utc(tx["timestamp"]) <= cutoff),
            token_created_at=datetime.fromisoformat(self.pair["creation_timestamp"]),
            token_created_block=self.pair["creation_block"], config=config,
        )

    def input_manifest(self, *, as_of=None, config=None, kind="early-buyer-replay"):
        cutoff = self.cutoff(as_of)
        pair = self.pair if self.pair and datetime.fromisoformat(self.pair["creation_timestamp"]) <= cutoff else None
        return manifest(kind=kind, parameters={
            "recipe": REPLAY_VERSION, "chain": self.chain, "token_address": self.token,
            "as_of": cutoff, "config": asdict(config or EarlyBuyerConfig()),
        }, facts={"pair": pair, "events": [
            fact for fact in self.facts if datetime.fromisoformat(fact["values"]["timestamp"]) <= cutoff
        ]}, notes=[] if pair else ["UNKNOWN_CANONICAL_CREATION"])


def load_replay_inputs(session, token, *, as_of, chain="bsc", entry_only=False, event_limit=None):
    if chain != "bsc":
        raise ValueError("canonical early-buyer replay supports bsc only")
    as_of, token = utc(as_of), token.lower()
    epoch = capture_epoch(session, chain)
    pair = session.scalar(select(Pair).where(
        Pair.chain_id == 56, or_(Pair.token0_address == token, Pair.token1_address == token),
        Pair.creation_timestamp <= as_of, *canonical_fact_conditions(Pair),
    ).order_by(Pair.creation_timestamp, Pair.id).limit(1).execution_options(populate_existing=True))
    if pair is None:
        if capture_epoch(session, chain) != epoch:
            raise StaleReplayInputs("canonical epoch changed during early-buyer replay")
        return ReplayInputs(chain, token, as_of, epoch, None, (), ())
    events, source_limit = [], False
    for model, conditions in (
        (MarketEvent, (MarketEvent.chain_id == 56, MarketEvent.token_address == token)),
        (TradeEvent, (TradeEvent.chain == chain, TradeEvent.token_address == token)),
    ):
        query = select(model).where(
            *conditions, model.timestamp >= pair.creation_timestamp, model.timestamp <= as_of,
            *canonical_fact_conditions(model),
        )
        if entry_only:
            query = query.where(model.side == "BUY", model.timestamp <= min(
                as_of, utc(pair.creation_timestamp) + timedelta(hours=EarlyBuyerConfig().max_entry_hours),
            ))
            if model is MarketEvent:
                # A canonical Trade projection wins even when its corrected side
                # is SELL; filtering both sources to BUY must not invert priority.
                trade_exists = select(TradeEvent.id).where(
                    TradeEvent.chain == chain, TradeEvent.token_address == token,
                    TradeEvent.tx_hash == MarketEvent.tx_hash, TradeEvent.event_index == MarketEvent.event_index,
                    TradeEvent.timestamp >= pair.creation_timestamp, TradeEvent.timestamp <= as_of,
                    *canonical_fact_conditions(TradeEvent),
                ).exists()
                query = query.where(~trade_exists)
        if event_limit is not None:
            if not entry_only or event_limit < 1:
                raise ValueError("event_limit requires a positive entry-only bound")
            query = query.limit(event_limit + 1)
        selected = session.scalars(query.order_by(model.timestamp, model.block_number, model.event_index,
                                                   model.tx_hash, model.id)
                                    .execution_options(populate_existing=True)).all()
        source_limit |= event_limit is not None and len(selected) > event_limit
        events.extend(selected)
    # Trade projection wins a duplicate tx/log, including missing-price semantics.
    unique = {(row.tx_hash.lower(), row.event_index): row for row in events}
    ordered = sorted(unique.values(), key=lambda row: (utc(row.timestamp), row.block_number or 0,
                                                       row.tx_hash.lower(), row.event_index))
    txs, facts = [], []
    for row in ordered:
        txs.append({
            "wallet_address": row.trader_address if isinstance(row, TradeEvent) else row.wallet_address,
            "signature": row.tx_hash, "event_index": row.event_index, "timestamp": utc(row.timestamp),
            "side": getattr(row.side, "value", row.side), "token_address": token,
            "token_amount": normalized_amount(row),
            "usd_value": row.value_usd if isinstance(row, TradeEvent) else row.usd_value,
            "price": usd_price(row),
            "market_cap": row.market_cap if isinstance(row, TradeEvent) else (row.metadata_json or {}).get("market_cap"),
            "block_number": row.block_number, "metadata_json": row.metadata_json,
        })
        facts.append({"table": row.__tablename__, "values": row_values(row)})
    if capture_epoch(session, chain) != epoch:
        raise StaleReplayInputs("canonical epoch changed during early-buyer replay")
    # Each source is limited independently; reading a sentinel is conservative
    # even when the other source overlaps it. Never claim an unsaturated count.
    return ReplayInputs(chain, token, as_of, epoch, row_values(pair), tuple(txs), tuple(facts), entry_only, source_limit)


@dataclass(frozen=True)
class EarlyEntryResult:
    entries: tuple[dict, ...]
    reason: str | None
    source_epoch: int


def load_early_entries(session, token, *, as_of):
    """Bounded hot-path membership; never replay lifetime positions to count buyers."""
    inputs = load_replay_inputs(session, token, as_of=as_of, entry_only=True, event_limit=RADAR_ENTRY_EVENT_LIMIT)
    buyers = inputs._buyers()
    if inputs.source_limit and len(buyers) < EarlyBuyerConfig().effective_window_value:
        return EarlyEntryResult((), "EARLY_ENTRY_SOURCE_LIMIT", inputs.source_epoch)
    entries = tuple({"first_buy_time": row.first_buy_time, "wallet": row.wallet_address} for row in buyers)
    reason = None if entries else "NO_CANONICAL_EARLY_BUYERS"
    return EarlyEntryResult(entries, reason, inputs.source_epoch)

"""Phase 9.0's small, reproducible alpha-research baseline.

This module deliberately consumes only canonical Pair and TradeEvent facts.  It
does not repair market data, infer a price from a dust trade, or use a current
token outcome as an early feature.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from smms.db.models import Pair, Token, TradeEvent

from .vwap import WindowPoint, best_window_vwap, window_vwap

OBSERVATION_WINDOWS = ("30s", "1m", "5m")
WINDOWS = {"30s": timedelta(seconds=30), "1m": timedelta(minutes=1), "5m": timedelta(minutes=5)}
HORIZONS = {
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "6h": timedelta(hours=6),
    "24h": timedelta(hours=24),
    "3d": timedelta(days=3),
    "7d": timedelta(days=7),
}
FEATURE_VERSION = "alpha-features-v1"
LABEL_VERSION = "alpha-labels-v2"
UNKNOWN = None
LABEL_POSITIVE = "POSITIVE_CONFIRMED"
LABEL_NEGATIVE = "NEGATIVE_CONFIRMED"
LABEL_COVERAGE_UNKNOWN = "COVERAGE_UNKNOWN"
LABEL_ENTRY_UNAVAILABLE = "ENTRY_PRICE_UNAVAILABLE"
LABEL_DUST_ONLY = "DUST_ONLY"
PRICE_BASIS_HUMAN = "HUMAN_QUOTE_PRICE"
PRICE_BASIS_RAW = "RAW_PAIR_RATIO"
RAW_AMOUNT_UNRESOLVED = "RAW_AMOUNT_UNRESOLVED"
PRICE_BASIS_UNAVAILABLE = "ENTRY_PRICE_UNAVAILABLE"
PROVENANCE_GAPS = (
    "liquidity_usd",
    "liquidity_growth",
    "smart_money_buyers",
    "smart_money_volume",
    "smart_money_netflow",
    "bot_score",
    "honeypot",
    "lp_lock_status",
)


def swap_amounts_for_basis(
    event: TradeEvent, *, price_basis: str, pair: Pair | None = None,
) -> tuple[Decimal, Decimal] | None:
    """Resolve one swap in exactly one unit system; never fall back across bases."""
    if price_basis == PRICE_BASIS_RAW:
        recovered = derive_raw_swap_amounts(event, pair) if pair is not None else None
        return (recovered.raw_quote_amount, recovered.raw_target_token_amount) if recovered else None
    if price_basis == PRICE_BASIS_HUMAN:
        quote, token = _number(event.quote_amount), _number(event.token_amount)
        if quote is not None and token is not None and quote > 0 and token > 0:
            return quote, token
    return None


def _window_points(events, *, start, end, pair, price_basis):
    start, end = _utc(start), _utc(end)
    for event in events:
        timestamp = _utc(event.timestamp)
        if not start < timestamp <= end:
            continue
        amounts = swap_amounts_for_basis(event, price_basis=price_basis, pair=pair)
        quote = _number(event.quote_amount)
        if amounts is None or quote is None or quote <= 0:
            # An ineligible trade can still anchor an otherwise executable window.
            yield WindowPoint(timestamp)
        else:
            yield WindowPoint(timestamp, amounts[0], amounts[1], quote)


def rolling_vwap(events: Iterable[TradeEvent], *, start: datetime, end: datetime,
                 min_quote_notional: Decimal, pair: Pair | None = None,
                 price_basis: str = PRICE_BASIS_HUMAN) -> Decimal | None:
    """Exact totals in one basis, gated by known positive HUMAN quote notional."""
    return window_vwap(_window_points(events, start=start, end=end, pair=pair, price_basis=price_basis),
                       start=start, end=end, minimum=min_quote_notional)


def max_rolling_vwap(events: Iterable[TradeEvent], *, start: datetime, end: datetime,
                     min_quote_notional: Decimal, pair: Pair | None = None,
                     price_basis: str = PRICE_BASIS_HUMAN) -> tuple[Decimal | None, datetime | None]:
    """Prefer executable30s windows, then60s; sort once and slide exact totals."""
    return best_window_vwap(_window_points(events, start=start, end=end, pair=pair, price_basis=price_basis),
                            start=start, end=end, minimum=min_quote_notional)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _number(value: Any) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _side(value: Any) -> str:
    return str(getattr(value, "value", value)).upper()


def _safe_ratio(numerator: Decimal | int, denominator: Decimal | int) -> Decimal | None:
    denominator = Decimal(denominator)
    return Decimal(numerator) / denominator if denominator else None


def _serialize(value: Any) -> Any:
    if isinstance(value, datetime):
        return _utc(value).isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize(item) for item in value]
    return value


@dataclass(frozen=True)
class RawSwapAmounts:
    """Amounts recovered in the immutable raw pair units from a V2 Swap."""

    side: str
    raw_target_token_amount: Decimal
    raw_quote_amount: Decimal


@dataclass(frozen=True)
class CoverageChunk:
    """One requested historical range and its persistence outcome."""

    start_block: int
    end_block: int
    requested: bool = True
    successful: bool = False
    persisted: bool = False
    decode_complete: bool = True
    raw_log_count: int = 0
    decoded_swap_count: int = 0
    pair_created_query_success: bool = True
    swap_query_success: bool = True
    pair_created_raw_count: int = 0
    swap_raw_count: int = 0
    error: str | None = None

    @property
    def covered(self) -> bool:
        # A successful empty response is still a covered chunk.
        return (
            self.requested
            and self.successful
            and self.persisted
            and self.decode_complete
            and self.pair_created_query_success
            and self.swap_query_success
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> CoverageChunk:
        return cls(**{key: value[key] for key in cls.__dataclass_fields__ if key in value})


def coverage_chunks_verified(chunks: Iterable[CoverageChunk]) -> bool:
    """Return true only when every requested chunk was successfully persisted."""
    requested = list(chunks)
    return bool(requested) and all(chunk.covered for chunk in requested)


def derive_raw_swap_amounts(trade: TradeEvent, pair: Pair) -> RawSwapAmounts | None:
    """Recover quote/target amounts without assuming target-token decimals.

    PancakeSwap V2 emits exactly one positive input and one positive output for
    a normal swap.  A malformed direction is deliberately unresolved rather
    than guessed from the largest amount.
    """
    quote = str(pair.quote_token or "").lower()
    token0 = str(pair.token0_address or "").lower()
    token1 = str(pair.token1_address or "").lower()
    if not quote or quote not in {token0, token1}:
        return None
    raw = (trade.metadata_json or {}).get("amounts_raw")
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    try:
        amount0_in, amount1_in, amount0_out, amount1_out = (
            Decimal(str(value)) for value in raw
        )
    except (InvalidOperation, TypeError, ValueError):
        return None
    amounts = (amount0_in, amount1_in, amount0_out, amount1_out)
    if any(value < 0 or not value.is_finite() for value in amounts):
        return None
    quote_in, quote_out = (
        (amount0_in, amount0_out) if quote == token0 else (amount1_in, amount1_out)
    )
    target_in, target_out = (
        (amount1_in, amount1_out) if quote == token0 else (amount0_in, amount0_out)
    )
    if quote_in > 0 and target_out > 0 and quote_out == 0 and target_in == 0:
        inferred = "BUY"
        result = RawSwapAmounts(inferred, target_out, quote_in)
    elif target_in > 0 and quote_out > 0 and quote_in == 0 and target_out == 0:
        inferred = "SELL"
        result = RawSwapAmounts(inferred, target_in, quote_out)
    else:
        return None
    declared = _side(trade.side)
    if declared not in {"UNKNOWN", inferred}:
        return None
    return result


@dataclass(frozen=True)
class ResearchCoverageManifest:
    """Minimal auditable contract for a complete historical scan."""

    chain_id: int
    protocol: str
    factory: str | None
    start_block: int
    end_block: int
    start_timestamp: datetime
    end_timestamp: datetime
    pair_created_query_complete: bool
    swap_query_complete: bool
    relevant_pair_count: int
    raw_log_count: int
    decoded_swap_count: int
    rpc_source: str
    query_parameters: dict[str, Any]
    started_at: datetime
    completed_at: datetime
    status: str
    relevant_pair_addresses: frozenset[str] = frozenset()
    schema_version: int = 1
    chunks: tuple[CoverageChunk, ...] = ()
    pair_created_raw_count: int = 0
    swap_raw_count: int = 0

    @classmethod
    def from_chunks(
        cls,
        *,
        chain_id: int,
        protocol: str,
        factory: str | None,
        start_block: int,
        end_block: int,
        start_timestamp: datetime,
        end_timestamp: datetime,
        pair_created_query_complete: bool,
        swap_query_complete: bool,
        relevant_pair_count: int,
        rpc_source: str,
        query_parameters: dict[str, Any],
        started_at: datetime,
        completed_at: datetime,
        chunks: Iterable[CoverageChunk],
        relevant_pair_addresses: Iterable[str] = (),
    ) -> ResearchCoverageManifest:
        chunk_rows = list(chunks)
        pair_addresses = frozenset(str(address).lower() for address in relevant_pair_addresses)
        return cls(
            chain_id=chain_id,
            protocol=protocol,
            factory=factory,
            start_block=start_block,
            end_block=end_block,
            start_timestamp=start_timestamp,
            end_timestamp=end_timestamp,
            pair_created_query_complete=pair_created_query_complete,
            swap_query_complete=swap_query_complete,
            relevant_pair_count=relevant_pair_count,
            raw_log_count=sum(row.raw_log_count for row in chunk_rows),
            decoded_swap_count=sum(row.decoded_swap_count for row in chunk_rows),
            rpc_source=rpc_source,
            query_parameters=query_parameters,
            started_at=started_at,
            completed_at=completed_at,
            status=(
                "VERIFIED"
                if pair_created_query_complete
                and swap_query_complete
                and coverage_chunks_verified(chunk_rows)
                and len(pair_addresses) == relevant_pair_count
                else "INCOMPLETE"
            ),
            relevant_pair_addresses=pair_addresses,
            pair_created_raw_count=sum(row.pair_created_raw_count for row in chunk_rows),
            swap_raw_count=sum(row.swap_raw_count for row in chunk_rows),
            chunks=tuple(chunk_rows),
        )

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["relevant_pair_addresses"] = sorted(self.relevant_pair_addresses)
        value["chunks"] = [chunk.as_dict() for chunk in self.chunks]
        return _serialize(value)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ResearchCoverageManifest:
        if value.get("schema_version") != 1:
            raise ValueError("unsupported coverage manifest schema_version")
        chunks = tuple(CoverageChunk.from_dict(item) for item in value.get("chunks", ()))
        def parse_datetime(name: str) -> datetime:
            parsed = datetime.fromisoformat(str(value[name]))
            return _utc(parsed)

        return cls(
            **{
                key: value[key]
                for key in (
                    "chain_id", "protocol", "factory", "start_block", "end_block",
                    "pair_created_query_complete",
                    "swap_query_complete", "relevant_pair_count", "raw_log_count",
                    "decoded_swap_count", "rpc_source", "query_parameters", "status",
                )
            },
            start_timestamp=parse_datetime("start_timestamp"),
            end_timestamp=parse_datetime("end_timestamp"),
            started_at=parse_datetime("started_at"),
            completed_at=parse_datetime("completed_at"),
            relevant_pair_addresses=frozenset(
                str(address).lower() for address in value.get("relevant_pair_addresses", ())
            ),
            schema_version=1,
            chunks=chunks,
            pair_created_raw_count=int(value.get("pair_created_raw_count", 0)),
            swap_raw_count=int(value.get("swap_raw_count", 0)),
        )

    def write_json(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.as_dict(), indent=2), encoding="utf-8")

    @classmethod
    def read_json(
        cls,
        path: str | Path,
        *,
        chain_id: int = 56,
        protocol: str = "pancakeswap-v2",
        start_block: int | None = None,
        end_block: int | None = None,
    ) -> ResearchCoverageManifest:
        manifest = cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
        if manifest.chain_id != chain_id or manifest.protocol != protocol:
            raise ValueError("coverage manifest chain/protocol mismatch")
        if start_block is not None and manifest.start_block > start_block:
            raise ValueError("coverage manifest starts after requested range")
        if end_block is not None and manifest.end_block < end_block:
            raise ValueError("coverage manifest ends before requested range")
        if manifest.status == "VERIFIED" and (
            manifest.relevant_pair_count > 0
            and len(manifest.relevant_pair_addresses) != manifest.relevant_pair_count
        ):
            raise ValueError("verified manifest must explicitly list all relevant pair addresses")
        return manifest

    def covers(self, pair: Pair, horizon_end: datetime) -> bool:
        if pair.creation_timestamp is None or pair.creation_block is None:
            return False
        return (
            self.status == "VERIFIED"
            and self.pair_created_query_complete
            and self.swap_query_complete
            and self.start_block <= pair.creation_block <= self.end_block
            and self.start_timestamp <= _utc(pair.creation_timestamp)
            and _utc(horizon_end) <= _utc(self.end_timestamp)
            and pair.chain_id == self.chain_id
            and self.protocol == "pancakeswap-v2"
            and pair.dex.lower() == "pancakeswap"
            and pair.address.lower() in self.relevant_pair_addresses
        )


@dataclass(frozen=True)
class AlphaSample:
    token_address: str
    pair_address: str
    quote_token: str | None
    quote_symbol: str | None
    price_unit: str | None
    t0: datetime
    observation_window: str
    observation_cutoff: datetime
    feature_timestamp: datetime
    future_label_timestamp: datetime | None
    data_source: str
    feature_version: str
    label_version: str
    entry_price: Decimal | None
    entry_price_method: str
    price_basis: str
    feature_evidence: tuple[dict[str, Any], ...]
    label_evidence: tuple[dict[str, Any], ...]
    features: dict[str, Any]
    labels: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return _serialize(asdict(self))


class AlphaDatasetBuilder:
    """Build exactly one point-in-time sample per pair/token/window.

    ``TradeEvent.timestamp <= observation_cutoff`` is the hard feature
    boundary.  Future events are fetched only after feature construction and
    are placed exclusively in labels.
    """

    def __init__(
        self,
        session: Session,
        *,
        chain: str = "bsc",
        min_entry_quote: Decimal = Decimal("0.01"),
        min_future_quote: Decimal = Decimal("0.01"),
        coverage_resolver: Callable[[Pair, datetime], bool] | None = None,
        coverage_manifest: ResearchCoverageManifest | None = None,
    ) -> None:
        self.session = session
        self.chain = chain
        self.min_entry_quote = min_entry_quote
        self.min_future_quote = min_future_quote
        # The live checkpoint has a block cursor, but no durable block-time
        # coverage contract. Callers may supply a verified historical barrier.
        self.coverage_manifest = coverage_manifest
        self.coverage_resolver = coverage_resolver or (
            (lambda pair, end: coverage_manifest.covers(pair, end))
            if coverage_manifest is not None
            else (lambda _pair, _end: False)
        )

    def build(
        self, start: datetime, end: datetime, *, windows: Iterable[str] = OBSERVATION_WINDOWS
    ) -> list[AlphaSample]:
        start, end = _utc(start), _utc(end)
        requested = tuple(windows)
        if start > end:
            raise ValueError("start must be before end")
        if unknown := set(requested) - WINDOWS.keys():
            raise ValueError(f"unsupported observation windows: {', '.join(sorted(unknown))}")
        pairs = list(
            self.session.scalars(
                select(Pair)
                .where(
                    Pair.chain_id == 56,
                    Pair.dex == "pancakeswap",
                    Pair.canonical.is_(True),
                    Pair.removed.is_(False),
                    Pair.creation_timestamp >= start,
                    Pair.creation_timestamp <= end,
                )
                .order_by(Pair.creation_timestamp, Pair.id)
            ).all()
        )
        token_rows = self.session.scalars(select(Token).where(Token.chain == self.chain)).all()
        tokens_by_address = {row.address.lower(): row for row in token_rows}
        samples: list[AlphaSample] = []
        for pair in pairs:
            if pair.creation_timestamp is None:
                continue
            t0 = _utc(pair.creation_timestamp)
            events = list(
                self.session.scalars(
                    select(TradeEvent)
                    .where(
                        TradeEvent.chain == self.chain,
                        TradeEvent.pair_address == pair.address.lower(),
                        TradeEvent.canonical.is_(True),
                        TradeEvent.removed.is_(False),
                        TradeEvent.timestamp <= t0 + max(HORIZONS.values()),
                    )
                    .order_by(TradeEvent.timestamp, TradeEvent.id)
                ).all()
            )
            tokens = self._pair_tokens(pair, events)
            for token in tokens:
                token_events = [event for event in events if event.token_address.lower() == token]
                for window in requested:
                    cutoff = t0 + WINDOWS[window]
                    samples.append(
                        self._sample(
                            token,
                            pair,
                            t0,
                            window,
                            cutoff,
                            token_events,
                            tokens_by_address,
                        )
                    )
        return samples

    @staticmethod
    def _pair_tokens(pair: Pair, events: list[TradeEvent]) -> list[str]:
        """Keep no-swap pairs in the universe without treating the quote as a Meme.

        PairCreated is the inclusion fact.  A token with no swap is a valid
        negative/unknown sample, rather than a row to silently discard.
        """
        pair_tokens = {pair.token0_address.lower(), pair.token1_address.lower()}
        if pair.quote_token:
            pair_tokens.discard(pair.quote_token.lower())
        observed = {event.token_address.lower() for event in events}
        return sorted(observed or pair_tokens)

    def _sample(
        self,
        token: str,
        pair: Pair,
        t0: datetime,
        window: str,
        cutoff: datetime,
        events: list[TradeEvent],
        tokens_by_address: dict[str, Token],
    ) -> AlphaSample:
        observed = [event for event in events if t0 <= _utc(event.timestamp) <= cutoff]
        future = [event for event in events if _utc(event.timestamp) > cutoff]
        features = self._features(observed, t0, cutoff)
        entry_price, price_basis = self._entry_price(observed, pair)
        horizon_coverage = {
            name: bool(self.coverage_resolver(pair, cutoff + delta))
            for name, delta in HORIZONS.items()
        }
        labels, label_at = self._labels(
            future, cutoff, entry_price, price_basis, pair, horizon_coverage,
            min_future_quote=self.min_future_quote,
        )
        quote = pair.quote_token.lower() if pair.quote_token else None
        quote_row = tokens_by_address.get(quote) if quote else None
        token_row = tokens_by_address.get(token)
        quote_symbol = quote_row.symbol if quote_row and quote_row.symbol else None
        token_symbol = token_row.symbol if token_row and token_row.symbol else None
        return AlphaSample(
            token,
            pair.address.lower(),
            quote,
            quote_symbol,
            f"{quote_symbol} per {token_symbol}" if quote_symbol and token_symbol else None,
            t0,
            window,
            cutoff,
            cutoff,
            label_at,
            "trade_events",
            FEATURE_VERSION,
            LABEL_VERSION,
            entry_price,
            (
                "quote_vwap_min_quote"
                if price_basis == PRICE_BASIS_HUMAN
                else "raw_quote_vwap_min_quote"
                if price_basis == PRICE_BASIS_RAW
                else "RAW_AMOUNT_UNRESOLVED"
                if price_basis == RAW_AMOUNT_UNRESOLVED
                else "UNKNOWN"
            ),
            price_basis,
            tuple(self._event_reference(event) for event in observed),
            tuple(self._event_reference(event) for event in future),
            features,
            labels,
        )

    @staticmethod
    def _event_reference(event: TradeEvent) -> dict[str, Any]:
        return {
            "tx_hash": event.tx_hash,
            "block_number": event.block_number,
            "event_index": event.event_index,
            "trader_address": event.trader_address,
            "price_quote": event.price_quote,
            "amounts_raw": (event.metadata_json or {}).get("amounts_raw"),
            "quote_amount": event.quote_amount,
            "timestamp": _utc(event.timestamp),
        }

    def _entry_price(
        self, events: list[TradeEvent], pair: Pair
    ) -> tuple[Decimal | None, str]:
        human: list[tuple[Decimal, Decimal]] = []
        raw: list[tuple[Decimal, Decimal]] = []
        eligible = 0
        for event in events:
            quote, token = _number(event.quote_amount), _number(event.token_amount)
            if quote is None or quote < self.min_entry_quote:
                continue
            eligible += 1
            if token is not None and token > 0 and _number(event.price_quote) is not None:
                human.append((quote, token))
            recovered = derive_raw_swap_amounts(event, pair)
            if recovered is not None:
                raw.append((recovered.raw_quote_amount, recovered.raw_target_token_amount))
        quantities, basis = (
            (human, PRICE_BASIS_HUMAN)
            if human and len(human) == eligible
            else (raw, PRICE_BASIS_RAW)
            if raw and len(raw) == eligible
            else ([], RAW_AMOUNT_UNRESOLVED if eligible else PRICE_BASIS_UNAVAILABLE)
        )
        total_quote = sum((quote for quote, _ in quantities), Decimal(0))
        total_token = sum((token for _, token in quantities), Decimal(0))
        if not total_quote or not total_token:
            return None, basis
        return total_quote / total_token, basis

    def _features(self, events: list[TradeEvent], t0: datetime, cutoff: datetime) -> dict[str, Any]:
        buys = [event for event in events if _side(event.side) == "BUY"]
        sells = [event for event in events if _side(event.side) == "SELL"]
        buyers, sellers = (
            {event.trader_address.lower() for event in buys},
            {event.trader_address.lower() for event in sells},
        )
        values = [_number(event.quote_amount or event.native_amount) for event in events]
        volume_known = all(value is not None for value in values) if events else True
        amounts = [value for value in values if value is not None]
        midpoint = t0 + (cutoff - t0) / 2
        first_buyers = {
            event.trader_address.lower() for event in buys if _utc(event.timestamp) <= midpoint
        }
        second_buyers = {
            event.trader_address.lower() for event in buys if _utc(event.timestamp) > midpoint
        }
        buy_notional: dict[str, Decimal] = defaultdict(Decimal)
        buy_values: list[Decimal | None] = []
        for event in buys:
            value = _number(event.quote_amount or event.native_amount)
            buy_values.append(value)
            if value is not None:
                buy_notional[event.trader_address.lower()] += value
        total_buy = sum(buy_notional.values(), Decimal(0))
        shares = (
            sorted((value / total_buy for value in buy_notional.values()), reverse=True)
            if total_buy
            else []
        )
        block_counts = Counter(
            event.block_number for event in events if event.block_number is not None
        )
        same_block = sum(count for count in block_counts.values() if count > 1)
        amount_counts = Counter(amounts)
        identical = sum(count for count in amount_counts.values() if count > 1)
        by_wallet: dict[str, list[datetime]] = defaultdict(list)
        for event in buys:
            by_wallet[event.trader_address.lower()].append(_utc(event.timestamp))
        rapid = sum(
            1
            for timestamps in by_wallet.values()
            for a, b in zip(sorted(timestamps), sorted(timestamps)[1:])
            if (b - a).total_seconds() <= 3
        )
        return {
            "trade_count": len(events),
            "buy_count": len(buys),
            "sell_count": len(sells),
            "buy_sell_ratio": _safe_ratio(len(buys), len(sells)),
            "buy_sell_imbalance": _safe_ratio(len(buys) - len(sells), len(events)),
            "unique_wallets": len(buyers | sellers),
            "unique_buyers": len(buyers),
            "unique_sellers": len(sellers),
            "buyer_growth_rate": _safe_ratio(
                len(second_buyers) - len(first_buyers), len(first_buyers)
            ),
            "volume_quote": sum(amounts, Decimal(0)) if volume_known else UNKNOWN,
            "buy_volume_quote": sum(buy_notional.values(), Decimal(0))
            if all(value is not None for value in buy_values)
            else UNKNOWN,
            "top1_buyer_share": shares[0] if shares else UNKNOWN,
            "top5_buyer_share": sum(shares[:5], Decimal(0)) if shares else UNKNOWN,
            "top10_buyer_share": sum(shares[:10], Decimal(0)) if shares else UNKNOWN,
            "wallet_concentration": sum(share * share for share in shares) if shares else UNKNOWN,
            "rapid_fire_ratio": _safe_ratio(rapid, len(buys)),
            "identical_amount_ratio": _safe_ratio(identical, len(amounts)),
            "same_block_trade_ratio": _safe_ratio(same_block, len(events)),
            # USD liquidity, contract risk and smart-wallet reputation remain UNKNOWN
            # until a source with historical as-of provenance exists.
            "liquidity_usd": UNKNOWN,
            "liquidity_growth": UNKNOWN,
            "smart_money_buyers": UNKNOWN,
            "smart_money_volume": UNKNOWN,
            "smart_money_netflow": UNKNOWN,
            "bot_score": UNKNOWN,
            "honeypot": UNKNOWN,
            "lp_lock_status": UNKNOWN,
        }

    def _labels(
        self,
        future: list[TradeEvent],
        cutoff: datetime,
        entry: Decimal | None,
        price_basis: str,
        pair: Pair,
        horizon_coverage: dict[str, bool],
        *,
        min_future_quote: Decimal,
    ) -> tuple[dict[str, Any], datetime | None]:
        labels: dict[str, Any] = {
            "rugged": UNKNOWN,
            "liquidity_removed": UNKNOWN,
            "honeypot": UNKNOWN,
            "dead_token": UNKNOWN,
        }
        if entry is None:
            for name in HORIZONS:
                labels[f"max_return_{name}"] = UNKNOWN
                labels[f"max_realisable_return_{name}"] = UNKNOWN
                labels[f"max_realisable_return_{name}_status"] = LABEL_ENTRY_UNAVAILABLE
                labels[f"max_return_{name}_status"] = LABEL_ENTRY_UNAVAILABLE
            for target in (2, 3, 5, 10, 20, 50, 100):
                labels[f"reached_{target}x"] = UNKNOWN
                for name in HORIZONS:
                    labels[f"reached_{target}x_{name}"] = UNKNOWN
                    labels[f"reached_{target}x_{name}_status"] = LABEL_ENTRY_UNAVAILABLE
            return labels, None
        label_times: list[datetime] = []
        highs: dict[str, Decimal | None] = {}
        realisable_highs: dict[str, Decimal | None] = {}
        for name, delta in HORIZONS.items():
            path = []
            for event in future:
                if _utc(event.timestamp) > cutoff + delta:
                    continue
                if price_basis == PRICE_BASIS_HUMAN:
                    price = _number(event.price_quote)
                elif price_basis == PRICE_BASIS_RAW:
                    recovered = derive_raw_swap_amounts(event, pair)
                    price = (
                        recovered.raw_quote_amount / recovered.raw_target_token_amount
                        if recovered is not None and recovered.raw_target_token_amount > 0
                        else None
                    )
                else:
                    price = None
                if price is not None:
                    path.append((event, price))
            path = [(event, price) for event, price in path if price is not None and price > 0]
            if not path:
                labels[f"max_return_{name}"] = UNKNOWN
                labels[f"max_return_{name}_status"] = (
                    LABEL_NEGATIVE if horizon_coverage[name] else LABEL_COVERAGE_UNKNOWN
                )
                highs[name] = None
                realisable_highs[name] = None
                labels[f"max_realisable_return_{name}"] = UNKNOWN
                labels[f"max_realisable_return_{name}_status"] = (
                    LABEL_NEGATIVE if horizon_coverage[name] else LABEL_COVERAGE_UNKNOWN
                )
                continue
            winner, high = max(path, key=lambda item: item[1])
            highs[name] = high
            labels[f"max_return_{name}"] = high / entry - 1 if horizon_coverage[name] else UNKNOWN
            labels[f"max_return_{name}_status"] = (
                LABEL_NEGATIVE if horizon_coverage[name] else LABEL_COVERAGE_UNKNOWN
            )
            # Use a rolling VWAP in the future horizon.  Keep the old tick
            # maximum for diagnostics, but never let a dust execution define
            # the research label.
            realised, _realised_at = max_rolling_vwap(
                future, start=cutoff, end=cutoff + delta,
                min_quote_notional=min_future_quote,
                pair=pair, price_basis=price_basis)
            realisable_highs[name] = realised
            labels[f"max_realisable_return_{name}"] = (
                realised / entry - 1 if realised is not None and horizon_coverage[name] else UNKNOWN
            )
            labels[f"max_realisable_return_{name}_status"] = (
                LABEL_NEGATIVE if realised is not None and horizon_coverage[name]
                else LABEL_COVERAGE_UNKNOWN if not horizon_coverage[name]
                else LABEL_DUST_ONLY
            )
            label_times.append(_utc(winner.timestamp))
        for target in (2, 3, 5, 10, 20, 50, 100):
            confirmed = [
                high for high in realisable_highs.values()
                if high is not None and high >= entry * target
            ]
            labels[f"reached_{target}x"] = True if confirmed else UNKNOWN
            for name in HORIZONS:
                high = realisable_highs[name]
                if high is not None and high >= entry * target:
                    labels[f"reached_{target}x_{name}"] = True
                    labels[f"reached_{target}x_{name}_status"] = LABEL_POSITIVE
                elif horizon_coverage[name]:
                    labels[f"reached_{target}x_{name}"] = False
                    labels[f"reached_{target}x_{name}_status"] = (
                        LABEL_DUST_ONLY
                        if highs[name] is not None and highs[name] >= entry * target
                        else LABEL_NEGATIVE
                    )
                else:
                    labels[f"reached_{target}x_{name}"] = UNKNOWN
                    labels[f"reached_{target}x_{name}_status"] = LABEL_COVERAGE_UNKNOWN
        confirmed_returns = [high / entry - 1 for high in realisable_highs.values() if high is not None]
        labels["rugged"] = (
            any(value <= Decimal("-0.90") for value in confirmed_returns)
            if confirmed_returns
            else UNKNOWN
        )
        return labels, max(label_times) if label_times else None


class AlphaBacktest:
    """Uncalibrated rule baseline plus transparent, bucketed evaluation."""

    def __init__(self, samples: Iterable[AlphaSample]) -> None:
        self.samples = list(samples)

    @staticmethod
    def score(sample: AlphaSample) -> Decimal | None:
        f = sample.features
        if f["trade_count"] == 0:
            return None
        parts: list[Decimal] = []
        parts.append(min(Decimal(1), Decimal(f["unique_buyers"]) / Decimal(10)))
        imbalance = f["buy_sell_imbalance"]
        if imbalance is not None:
            parts.append((imbalance + 1) / 2)
        concentration = f["top5_buyer_share"]
        if concentration is not None:
            parts.append(max(Decimal(0), 1 - concentration))
        rapid = f["rapid_fire_ratio"]
        if rapid is not None:
            parts.append(max(Decimal(0), 1 - rapid))
        return (sum(parts, Decimal(0)) / len(parts) * 100).quantize(Decimal("0.01"))

    def report(self, *, observation_window: str = "1m", horizon: str = "1h") -> dict[str, Any]:
        rows = [
            sample for sample in self.samples if sample.observation_window == observation_window
        ]
        if horizon not in HORIZONS:
            raise ValueError(f"unsupported future horizon: {horizon}")
        target_key = f"reached_10x_{horizon}"
        rug_key = "rugged"
        scored = [(sample, self.score(sample)) for sample in rows]
        labelled = [
            (sample, score) for sample, score in scored if sample.labels.get(target_key) is not None
        ]
        return {
            "report": "PHASE 9 ALPHA BASELINE REPORT",
            "status": "OK" if labelled else "LABEL_PIPELINE_BLOCKED",
            "dataset": {
                "sample_count": len(rows),
                "trade_event_count": sum(sample.features["trade_count"] for sample in rows),
                "token_count": len({sample.token_address for sample in rows}),
                "date_range": self._date_range(rows),
                "positive_labels": sum(
                    bool(sample.labels.get(target_key)) for sample, _ in labelled
                ),
                "negative_labels": sum(
                    not bool(sample.labels.get(target_key)) for sample, _ in labelled
                ),
                "price_label_coverage": len(labelled),
                "entry_price_available": sum(sample.entry_price is not None for sample in rows),
                "entry_price_unavailable": sum(sample.entry_price is None for sample in rows),
                "price_basis_counts": dict(
                    Counter(sample.price_basis for sample in rows)
                ),
                "human_quote_price_usable": sum(
                    sample.price_basis == PRICE_BASIS_HUMAN for sample in rows
                ),
                "raw_pair_ratio_usable": sum(
                    sample.price_basis == PRICE_BASIS_RAW for sample in rows
                ),
                "raw_amount_unresolved": sum(
                    sample.price_basis == RAW_AMOUNT_UNRESOLVED for sample in rows
                ),
                "unknown_labels": len(rows) - len(labelled),
                "coverage_unknown": sum(
                    sample.labels.get(f"reached_10x_{horizon}_status") == LABEL_COVERAGE_UNKNOWN
                    for sample in rows
                ),
            },
            "label_summary_by_horizon": self._label_summary(rows),
            "label_definition": {
                "entry_reference": (
                    "quote_amount / token_amount for observation-window trades; "
                    "dust quote_amount < 0.01 is excluded"
                ),
                "observation_window": observation_window,
                "future_horizon": horizon,
                "targets": ["2x", "3x", "5x", "10x", "20x", "50x", "100x"],
                "right_censoring": "negative requires verified canonical horizon coverage; positives are definitive",
            },
            "features": {
                "implemented": self._implemented_features(rows),
                "unknown_due_to_missing_provenance": list(PROVENANCE_GAPS),
                "missing": [
                    "historical_winner_wallet_overlap",
                    "USD liquidity",
                    "contract/LP risk evidence",
                ],
            },
            "feature_analysis": self._feature_analysis(rows, target_key, rug_key, horizon),
            "backtest": self._backtest(scored, labelled, target_key, rug_key, horizon),
            "hit_tokens": self._hits(labelled, target_key, horizon),
            "rug_filtering_performance": {
                "status": "INSUFFICIENT_PRICE_COVERAGE",
                "reason": "No price path is available to establish a -90% rug proxy.",
            },
            "false_positives": self._false_positives(scored, target_key, rug_key),
            "false_negatives": self._false_negatives(scored, target_key),
            "data_leakage_checks": {
                "feature_boundary": "TradeEvent.timestamp <= observation_cutoff",
                "label_boundary": "TradeEvent.timestamp > observation_cutoff",
                "entry_reference": "observation-window-only VWAP",
                "time_split": "not applicable: this is a rule baseline without fitting",
                "passed": True,
            },
            "current_infrastructure_blockers": self._blockers(rows),
            "recommendation": (
                "Do not enter Phase 9.1. Use RAW_PAIR_RATIO for same-pair labels when human "
                "decimals are unavailable, and require an auditable canonical coverage manifest "
                "before treating a missing target as negative."
            ),
        }

    @staticmethod
    def _date_range(rows: list[AlphaSample]) -> dict[str, str | None]:
        return {
            "from": min((sample.t0 for sample in rows), default=None),
            "to": max((sample.t0 for sample in rows), default=None),
        }

    @staticmethod
    def _implemented_features(rows: list[AlphaSample]) -> list[str]:
        return sorted(
            {key for sample in rows for key in sample.features if key not in PROVENANCE_GAPS}
        )

    @staticmethod
    def _label_summary(rows: list[AlphaSample]) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for horizon in HORIZONS:
            valid = [
                sample
                for sample in rows
                if sample.labels.get(f"reached_10x_{horizon}") is not None
            ]
            result[horizon] = {
                "valid_labels": len(valid),
                "coverage_unknown": sum(
                    sample.labels.get(f"reached_10x_{horizon}_status") == LABEL_COVERAGE_UNKNOWN
                    for sample in rows
                ),
                **AlphaBacktest._target_metrics(valid, horizon),
            }
        return result

    def _feature_analysis(
        self, rows: list[AlphaSample], target: str, rug: str, horizon: str
    ) -> dict[str, Any]:
        definitions = {
            "unique_buyers": ((0, 0), (1, 1), (2, 2), (3, None)),
            "buy_sell_ratio": (
                (None, Decimal("0.5")),
                (Decimal("0.5"), Decimal(1)),
                (Decimal(1), Decimal(3)),
                (Decimal(3), None),
            ),
            "top5_buyer_share": (
                (None, Decimal("0.50")),
                (Decimal("0.50"), Decimal("0.80")),
                (Decimal("0.80"), None),
            ),
            "rapid_fire_ratio": (
                (None, Decimal("0.25")),
                (Decimal("0.25"), Decimal("0.75")),
                (Decimal("0.75"), None),
            ),
            "buyer_growth_rate": ((None, Decimal(0)), (Decimal(0), Decimal(1)), (Decimal(1), None)),
            "volume_quote": ((None, Decimal(1)), (Decimal(1), Decimal(10)), (Decimal(10), None)),
            "wallet_concentration": (
                (None, Decimal("0.5")),
                (Decimal("0.5"), Decimal("0.8")),
                (Decimal("0.8"), None),
            ),
        }
        analysis: dict[str, Any] = {
            name: self._buckets(rows, name, buckets, target, rug, horizon)
            for name, buckets in definitions.items()
        }
        for name in (
            "liquidity_usd",
            "liquidity_growth",
            "smart_money_buyers",
            "bot_score",
            "historical_winner_wallet_overlap",
        ):
            analysis[name] = {
                "status": "UNKNOWN",
                "reason": "No historical point-in-time source with the required provenance.",
            }
        return analysis

    @staticmethod
    def _buckets(
        rows: list[AlphaSample],
        name: str,
        buckets: tuple[tuple[Any, Any], ...],
        target: str,
        rug: str,
        horizon: str,
    ) -> list[dict[str, Any]]:
        result = []
        for index, (low, high) in enumerate(buckets):
            selected = [
                sample
                for sample in rows
                if sample.features.get(name) is not None
                and (low is None or sample.features[name] >= low)
                and (
                    high is None
                    or sample.features[name] < high
                    or (index == len(buckets) - 1 and sample.features[name] <= high)
                )
            ]
            known = [sample for sample in selected if sample.labels.get(target) is not None]
            returns = [
                sample.labels.get(f"max_realisable_return_{horizon}")
                for sample in known
                if sample.labels.get(f"max_return_{horizon}") is not None
            ]
            rug_known = [sample for sample in known if sample.labels.get(rug) is not None]
            result.append(
                {
                    "bucket": (
                        f"{low if low is not None else '-inf'}.."
                        f"{high if high is not None else '+inf'}"
                    ),
                    "sample_count": len(selected),
                    "labelled_count": len(known),
                    "median_return": sorted(returns)[len(returns) // 2] if returns else None,
                    "3x_rate": AlphaBacktest._rate(known, f"reached_3x_{horizon}"),
                    "10x_rate": AlphaBacktest._rate(known, target),
                    "rug_rate": AlphaBacktest._rate(rug_known, rug),
                }
            )
        return result

    @staticmethod
    def _rate(rows: list[AlphaSample], key: str) -> dict[str, Any]:
        denominator = len(rows)
        hits = sum(bool(sample.labels.get(key)) for sample in rows)
        return {
            "hits": hits,
            "denominator": denominator,
            "rate": Decimal(hits) / Decimal(denominator) if denominator else None,
        }

    def _backtest(
        self,
        scored: list[tuple[AlphaSample, Decimal | None]],
        labelled: list[tuple[AlphaSample, Decimal | None]],
        target: str,
        rug: str,
        horizon: str,
    ) -> dict[str, Any]:
        all_labelled = [sample for sample, _ in labelled]
        overall = self._target_metrics(all_labelled, horizon)
        baseline = overall["10x_hit_rate"]["rate"]
        report: dict[str, Any] = {"overall": overall, "top_buckets": {}}
        for percentile in (20, 10, 5, 1):
            eligible = sorted(
                ((sample, score) for sample, score in labelled if score is not None),
                key=lambda item: item[1],
                reverse=True,
            )
            count = max(1, (len(eligible) * percentile + 99) // 100) if eligible else 0
            top = [sample for sample, _ in eligible[:count]]
            metrics = self._target_metrics(top, horizon)
            rate = metrics["10x_hit_rate"]["rate"]
            report["top_buckets"][f"top_{percentile}%"] = {
                "sample_count": len(top),
                **metrics,
                "rug_rate": self._rate(top, rug),
                "lift_vs_baseline": rate / baseline
                if rate is not None and baseline not in (None, 0)
                else None,
            }
        return report

    @staticmethod
    def _target_metrics(rows: list[AlphaSample], horizon: str) -> dict[str, dict[str, Any]]:
        return {
            f"{target}x_hit_rate": AlphaBacktest._rate(rows, f"reached_{target}x_{horizon}")
            for target in (2, 3, 5, 10, 20, 50, 100)
        }

    @staticmethod
    def _hits(
        labelled: list[tuple[AlphaSample, Decimal | None]], target: str, horizon: str
    ) -> list[dict[str, Any]]:
        return [
            _serialize(
                {
                    "token_address": sample.token_address,
                    "pair_address": sample.pair_address,
                    "score": score,
                    f"max_return_{horizon}": sample.labels.get(f"max_realisable_return_{horizon}"),
                    f"max_tick_return_{horizon}": sample.labels.get(f"max_return_{horizon}"),
                    "feature_evidence": sample.feature_evidence,
                    "label_evidence": sample.label_evidence,
                }
            )
            for sample, score in labelled
            if sample.labels.get(target) is True
        ]

    @staticmethod
    def _blockers(rows: list[AlphaSample]) -> list[str]:
        no_entry = sum(sample.entry_price is None for sample in rows)
        blockers = []
        if no_entry:
            blockers.append(
                f"{no_entry}/{len(rows)} selected samples lack a valid canonical entry price."
            )
        if rows and all(sample.features["liquidity_usd"] is None for sample in rows):
            blockers.append("No selected sample has point-in-time USD liquidity provenance.")
        if rows and all(sample.features["smart_money_buyers"] is None for sample in rows):
            blockers.append(
                "No selected sample has point-in-time smart-money reputation provenance."
            )
        return blockers

    @staticmethod
    def _false_positives(
        scored: list[tuple[AlphaSample, Decimal | None]], target: str, rug: str
    ) -> list[dict[str, Any]]:
        return [
            _serialize(
                {
                    "token_address": sample.token_address,
                    "pair_address": sample.pair_address,
                    "score": score,
                    "rugged": sample.labels.get(rug),
                    "max_return_1h": sample.labels.get("max_return_1h"),
                }
            )
            for sample, score in scored
            if score is not None and score >= 80 and sample.labels.get(target) is False
        ]

    @staticmethod
    def _false_negatives(
        scored: list[tuple[AlphaSample, Decimal | None]], target: str
    ) -> list[dict[str, Any]]:
        return [
            _serialize(
                {
                    "token_address": sample.token_address,
                    "pair_address": sample.pair_address,
                    "score": score,
                    "max_return_1h": sample.labels.get("max_return_1h"),
                }
            )
            for sample, score in scored
            if score is not None and score < 50 and sample.labels.get(target) is True
        ]

"""Small deterministic live Alpha scorer for the Phase 9.0.4 demo."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from time import perf_counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from smms.alpha.baseline import (
    PRICE_BASIS_HUMAN,
    PRICE_BASIS_RAW,
    PRICE_BASIS_UNAVAILABLE,
    max_rolling_vwap,
    rolling_vwap,
    swap_amounts_for_basis,
)
from smms.db.models import AlphaSnapshot, Pair, TradeEvent
from smms.observability import metrics

SNAPSHOT_WINDOWS = (30, 60, 180, 300)
SCORE_VERSION = "experimental-v2"
CREATION_LOOKBACK = timedelta(minutes=15)  # five-minute windows plus ingestion grace
TRACKING_HORIZON = timedelta(hours=1)
OUTCOME_GRACE = timedelta(minutes=15)
logger = logging.getLogger(__name__)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _decimal(value) -> Decimal | None:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _side(value) -> str:
    return str(getattr(value, "value", value)).upper().split(".")[-1]


def score_features(features: dict[str, object]) -> dict[str, object]:
    """Return ranking score, components and evidence without probability claims."""
    positive: list[str] = []
    negative: list[str] = []
    warnings: list[str] = ["UNCALIBRATED", "RESEARCH MODE", "NO VERIFIED 10X PROBABILITY"]
    buyers = features.get("unique_buyers")
    ratio = _decimal(features.get("buy_sell_ratio"))
    growth = _decimal(features.get("buyer_growth_rate"))
    volume_growth = _decimal(features.get("volume_growth"))
    liquidity = _decimal(features.get("liquidity"))
    top5 = _decimal(features.get("top5_buyer_share"))

    momentum = Decimal(0)
    if ratio is not None:
        momentum += min(Decimal(12), max(Decimal(0), ratio * 3))
        positive.append(f"buy/sell ratio = {ratio.quantize(Decimal('0.1'))}") if ratio >= 2 else None
    if growth is not None:
        momentum += min(Decimal(8), max(Decimal(0), growth * 8))
        if growth > 0:
            positive.append(f"buyer growth = {(growth * 100).quantize(Decimal(1))}%")
    if volume_growth is not None:
        momentum += min(Decimal(5), max(Decimal(0), volume_growth * 5))
        if volume_growth > 0:
            positive.append("quote volume accelerating")
    participation = min(Decimal(25), Decimal(int(buyers or 0)) * Decimal("0.8"))
    if buyers is not None:
        positive.append(f"unique buyers = {int(buyers)}") if int(buyers) >= 10 else None
    wallet = Decimal(10)
    if top5 is not None:
        wallet = max(Decimal(0), Decimal(20) - top5 * 20)
        if top5 > Decimal("0.4"):
            negative.append(f"top5 buyers = {(top5 * 100).quantize(Decimal(1))}%")
    else:
        warnings.append("holder concentration unavailable")
    liquidity_score = min(Decimal(15), max(Decimal(0), liquidity / 1000)) if liquidity is not None else Decimal(0)
    if liquidity is None:
        warnings.append("liquidity unavailable")
    elif liquidity < 10000:
        negative.append("liquidity still low")
    risk_quality = Decimal(0)
    warnings.append("risk data unavailable")
    rapid_fire = _decimal(features.get("rapid_fire_ratio"))
    if rapid_fire is not None and rapid_fire > Decimal("0.5"):
        risk_quality -= 6
        negative.append("rapid-fire trading elevated")
    score = max(0, min(100, int(momentum + participation + wallet + liquidity_score + risk_quality)))
    return {
        "alpha_score": score,
        "score_version": SCORE_VERSION,
        "risk_status": "UNKNOWN",
        "components": {
            "momentum": momentum,
            "participation": participation,
            "wallet_quality": wallet,
            "liquidity": liquidity_score,
            "risk_quality": risk_quality,
        },
        "positive_reasons": positive,
        "negative_reasons": negative,
        "warnings": warnings,
    }


def _features(events: list[TradeEvent], cutoff: datetime) -> dict[str, object]:
    buys = [e for e in events if _side(e.side) == "BUY"]
    sells = [e for e in events if _side(e.side) == "SELL"]
    buyers = {e.trader_address.lower() for e in buys if e.trader_address}
    by_wallet: dict[str, Decimal] = defaultdict(Decimal)
    volume = Decimal(0)
    first_volume = Decimal(0)
    midpoint = min(events, key=lambda e: _utc(e.timestamp)).timestamp if events else cutoff
    midpoint = _utc(midpoint) + (cutoff - _utc(midpoint)) / 2
    first_buyers = {e.trader_address.lower() for e in buys if _utc(e.timestamp) <= midpoint and e.trader_address}
    second_buyers = {e.trader_address.lower() for e in buys if _utc(e.timestamp) > midpoint and e.trader_address}
    for event in events:
        amount = _decimal(event.quote_amount)
        if amount is not None:
            volume += amount
            if _utc(event.timestamp) <= midpoint:
                first_volume += amount
        if _side(event.side) == "BUY" and amount is not None and event.trader_address:
            by_wallet[event.trader_address.lower()] += amount
    shares = sorted((x / sum(by_wallet.values(), Decimal(0)) for x in by_wallet.values()), reverse=True) if by_wallet else []
    return {
        "trade_count": len(events), "buy_count": len(buys), "sell_count": len(sells),
        "unique_buyers": len(buyers), "buy_sell_ratio": Decimal(len(buys)) / len(sells) if sells else None,
        "buyer_growth_rate": (Decimal(len(second_buyers)) - len(first_buyers)) / len(first_buyers) if first_buyers else None,
        "volume_quote": volume, "volume_growth": (volume - first_volume) / first_volume if first_volume else None,
        "top5_buyer_share": sum(shares[:5], Decimal(0)) if shares else None,
        "rapid_fire_ratio": None, "liquidity": None,
    }


def process_live_alpha(session: Session, *, now: datetime, minimum_quote: Decimal = Decimal("0.01")) -> int:
    """Materialize due snapshots and update their future shadow outcomes."""
    started = perf_counter()
    now = _utc(now)
    pairs = session.scalars(select(Pair).where(
        Pair.chain_id == 56, Pair.canonical.is_(True), Pair.removed.is_(False),
        Pair.creation_timestamp >= now - CREATION_LOOKBACK,
        Pair.creation_timestamp <= now,
    )).all()
    metrics.observe("alpha_pair_query_seconds", perf_counter() - started)
    created = 0
    for pair in pairs:
        if pair.creation_timestamp is None or not pair.quote_token:
            continue
        t0 = _utc(pair.creation_timestamp)
        token_address = next((a for a in (pair.token0_address, pair.token1_address) if a and a.lower() != pair.quote_token.lower()), None)
        if token_address is None:
            continue
        for seconds in SNAPSHOT_WINDOWS:
            snapshot_at = t0 + timedelta(seconds=seconds)
            if snapshot_at > now:
                continue
            existing = session.scalar(select(AlphaSnapshot).where(AlphaSnapshot.chain == "bsc", AlphaSnapshot.token_address == token_address.lower(), AlphaSnapshot.pair_address == pair.address.lower(), AlphaSnapshot.observation_seconds == seconds, AlphaSnapshot.score_version == SCORE_VERSION))
            if existing is not None:
                continue
            events = session.scalars(select(TradeEvent).where(TradeEvent.chain == "bsc", TradeEvent.pair_address == pair.address.lower(), TradeEvent.token_address == token_address.lower(), TradeEvent.timestamp >= t0, TradeEvent.timestamp <= snapshot_at, TradeEvent.canonical.is_(True), TradeEvent.removed.is_(False)).order_by(TradeEvent.timestamp, TradeEvent.id)).all()
            features = _features(events, snapshot_at)
            scoring = score_features(features)
            # Prefer human only when every priced observation has human amounts.
            eligible = [e for e in events if (_decimal(e.quote_amount) or 0) > 0]
            basis = PRICE_BASIS_HUMAN if eligible and all(
                swap_amounts_for_basis(e, price_basis=PRICE_BASIS_HUMAN) is not None
                for e in eligible
            ) else PRICE_BASIS_RAW
            entry = rolling_vwap(events, start=t0, end=snapshot_at,
                                 min_quote_notional=minimum_quote, pair=pair, price_basis=basis)
            if entry is None:
                basis = PRICE_BASIS_UNAVAILABLE
            features["risk_status"] = scoring["risk_status"]
            row = AlphaSnapshot(chain="bsc", token_address=token_address.lower(), pair_address=pair.address.lower(), pair_created_at=t0, snapshot_at=snapshot_at, age_seconds=seconds, observation_seconds=seconds, alpha_score=scoring["alpha_score"], score_version=SCORE_VERSION, scored_at=now, features_json={k: str(v) if isinstance(v, Decimal) else v for k, v in features.items()}, components_json={k: str(v) for k, v in scoring["components"].items()}, positive_reasons=scoring["positive_reasons"], negative_reasons=scoring["negative_reasons"], warnings=scoring["warnings"], entry_vwap=entry, outcome_json={
                "price_basis": basis,
                # Preserve sub-1e-18 raw ratios beyond the legacy Numeric column.
                "entry_vwap_exact": str(entry) if entry is not None else None,
                **{f"reached_{n}x": None for n in (2, 3, 5, 10)},
            })
            session.add(row)
            created += 1
    session.flush()
    examined, updated = _update_outcomes(session, now=now, minimum_quote=minimum_quote)
    duration = perf_counter() - started
    metrics.observe("alpha_iteration_seconds", duration)
    metrics.observe("alpha_pairs_examined", len(pairs))
    metrics.observe("alpha_snapshots_examined", examined)
    metrics.increment("alpha_outcomes_updated", updated)
    logger.info("alpha iteration duration=%.3fs pairs_examined=%s snapshots_examined=%s outcomes_updated=%s created=%s",
                duration, len(pairs), examined, updated, created)
    return created


def _update_outcomes(session: Session, *, now: datetime, minimum_quote: Decimal) -> tuple[int, int]:
    started = perf_counter()
    rows = session.scalars(select(AlphaSnapshot).where(
        AlphaSnapshot.score_version == SCORE_VERSION,
        AlphaSnapshot.snapshot_at >= now - TRACKING_HORIZON - OUTCOME_GRACE,
        AlphaSnapshot.snapshot_at <= now,
    )).all()
    updated = 0
    # One pair/event batch, bounded to the active one-hour shadow horizon.
    active = [r for r in rows if not (r.outcome_json or {}).get("tracking_complete")]
    addresses = {r.pair_address for r in active}
    pairs = {p.address: p for p in session.scalars(select(Pair).where(
        Pair.chain_id == 56, Pair.address.in_(addresses),
        Pair.canonical.is_(True), Pair.removed.is_(False),
    )).all()} if addresses else {}
    by_pair: dict[str, list[TradeEvent]] = defaultdict(list)
    if active:
        events = session.scalars(select(TradeEvent).where(
            TradeEvent.chain == "bsc", TradeEvent.pair_address.in_(addresses),
            TradeEvent.timestamp > min(_utc(r.snapshot_at) for r in active),
            TradeEvent.timestamp <= now,
            TradeEvent.canonical.is_(True), TradeEvent.removed.is_(False),
        ).order_by(TradeEvent.timestamp, TradeEvent.id)).all()
        for event in events:
            by_pair[event.pair_address].append(event)
    metrics.observe("alpha_outcome_batch_query_seconds", perf_counter() - started)
    for row in active:
        outcome = dict(row.outcome_json or {})
        entry = _decimal(outcome.get("entry_vwap_exact")) or row.entry_vwap
        basis = outcome.get("price_basis")
        end = min(now, _utc(row.snapshot_at) + TRACKING_HORIZON)
        if entry is None or entry <= 0 or basis not in {PRICE_BASIS_HUMAN, PRICE_BASIS_RAW}:
            continue
        pair = pairs.get(row.pair_address)
        if pair is None:
            continue
        events = [e for e in by_pair[row.pair_address] if e.token_address == row.token_address
                  and _utc(row.snapshot_at) < _utc(e.timestamp) <= end]
        prices = []
        for event in events:
            amounts = swap_amounts_for_basis(event, pair=pair, price_basis=basis)
            price = (amounts[0] / amounts[1] if amounts else None) if basis == PRICE_BASIS_RAW else _decimal(event.price_quote)
            if price is not None and price > 0:
                prices.append(price)
        row.current_return = prices[-1] / entry - 1 if prices else None
        row.max_tick_return = max(prices) / entry - 1 if prices else None
        realisable, _ = max_rolling_vwap(events, start=row.snapshot_at, end=end,
                                       min_quote_notional=minimum_quote, pair=pair, price_basis=basis)
        row.max_realisable_return = realisable / entry - 1 if realisable is not None else None
        row.outcome_json = {
            **outcome,
            **{f"reached_{n}x": (row.max_realisable_return >= n - 1
                                if row.max_realisable_return is not None else None)
               for n in (2, 3, 5, 10)},
            "tracking_horizon_seconds": int(TRACKING_HORIZON.total_seconds()),
            "evaluated_at": now.isoformat(),
            "tracking_complete": now >= _utc(row.snapshot_at) + TRACKING_HORIZON + OUTCOME_GRACE,
        }
        updated += 1
    return len(rows), updated

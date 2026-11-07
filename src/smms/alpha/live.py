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
from smms.db.facts import canonical_fact_conditions
from smms.db.models import AlphaCoverageJob, AlphaSnapshot, Pair, TradeEvent
from smms.observability import metrics

from .lineage import input_manifest
from .policy import LABEL_VERSION, SCORE_VERSION, TRACKING_HORIZON, policy_id
from .work import coverage_proof, repair_work

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
    elif features.get("buy_count", 0) and features.get("sell_count") == 0:
        momentum += 12
        positive.append("observed buys with no sells in covered window")
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
    wallet = Decimal(0)
    if top5 is not None:
        wallet = max(Decimal(0), Decimal(20) - top5 * 20)
        if top5 > Decimal("0.4"):
            negative.append(f"top5 buyers = {(top5 * 100).quantize(Decimal(1))}%")
    else:
        warnings.append("buyer concentration unavailable")
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
    # Observed activity has 70 available points. Missing liquidity does not
    # silently shrink a documented 0–100 activity scale. Risk stays UNKNOWN.
    capacity = Decimal(85) if liquidity is not None else Decimal(70)
    score = max(0, min(100, int((momentum + participation + wallet + liquidity_score + risk_quality) * 100 / capacity)))
    return {
        "alpha_score": score,
        "score_version": SCORE_VERSION,
        "risk_status": "UNKNOWN",
        "available_points": str(capacity),
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
        if amount is not None and amount > 0:
            volume += amount
            if _utc(event.timestamp) <= midpoint:
                first_volume += amount
        if _side(event.side) == "BUY" and amount is not None and amount > 0 and event.trader_address:
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


def process_live_alpha(session: Session, *, now: datetime, minimum_quote: Decimal = Decimal("0.01"),
                       batch_size: int = 128) -> int:
    """Recover bounded durable work, including tasks older than any live feed."""
    if not 1 <= batch_size <= 2000:
        raise ValueError("batch_size must be between 1 and 2000")
    policy_id(minimum_quote)
    started = perf_counter()
    now = _utc(now)
    repair_work(session, batch_size=batch_size)
    rows = session.scalars(select(AlphaSnapshot).where(
        AlphaSnapshot.score_version == SCORE_VERSION,
        AlphaSnapshot.task_status.in_(("PENDING_INPUT", "TRACKING")),
        AlphaSnapshot.next_evaluation_at <= now, AlphaSnapshot.snapshot_at <= now,
    ).order_by(AlphaSnapshot.next_evaluation_at, AlphaSnapshot.id).limit(batch_size)
        .with_for_update(skip_locked=True)).all()
    addresses = {row.pair_address for row in rows}
    pairs = {p.address: p for p in session.scalars(select(Pair).where(
        Pair.chain_id == 56, Pair.address.in_(addresses),
    ))} if addresses else {}
    scored = 0
    for row in rows:
        row.next_evaluation_at = now + timedelta(seconds=60)
        outcome = dict(row.outcome_json or {})
        # Freeze the execution policy on first attempt, before any outcome exists.
        notional = _decimal(outcome.get("minimum_quote")) or minimum_quote
        outcome.update(label_version=LABEL_VERSION, minimum_quote=str(notional),
                       policy_id=policy_id(notional), evaluated_at=now.isoformat(),
                       tracking_horizon_seconds=int(TRACKING_HORIZON.total_seconds()))
        pair = pairs.get(row.pair_address)
        if pair is None or not pair.canonical or pair.removed:
            row.task_status = "INVALIDATED"
            outcome.update(tracking_complete=False, input_status="NONCANONICAL_PAIR")
            row.outcome_json = outcome
            continue
        job = session.scalar(select(AlphaCoverageJob).where(AlphaCoverageJob.pair_id == pair.id))
        if job is None or row.coverage_generation != job.generation:
            row.task_status, row.next_evaluation_at = "INVALIDATED", None
            outcome.update(tracking_complete=False, input_status="STALE_GENERATION")
            row.outcome_json = outcome
            continue
        entry_proof = coverage_proof(session, pair, start=row.pair_created_at,
                                     end=row.snapshot_at, as_of=now)
        outcome["entry_coverage"] = entry_proof
        if entry_proof["status"] != "VERIFIED":
            outcome.update(input_status="AWAITING_ENTRY_COVERAGE", tracking_complete=False)
            row.outcome_json = outcome
            continue
        if row.coverage_generation is not None and row.coverage_generation != entry_proof["generation"]:
            row.task_status = "INVALIDATED"
            outcome.update(input_status="REBUILD_REQUIRED", tracking_complete=False)
            row.outcome_json = outcome
            continue
        if row.alpha_score is None:
            events = _trades(session, row, start=row.pair_created_at, end=row.snapshot_at)
            eligible = [e for e in events if (_decimal(e.quote_amount) or 0) > 0]
            basis = PRICE_BASIS_HUMAN if eligible and all(
                swap_amounts_for_basis(e, price_basis=PRICE_BASIS_HUMAN) is not None
                for e in eligible
            ) else PRICE_BASIS_RAW
            # Entry includes Swap logs in the PairCreated block. Future
            # evaluation retains its exclusive snapshot boundary.
            entry = rolling_vwap(events, start=_utc(row.pair_created_at) - timedelta(microseconds=1), end=row.snapshot_at,
                                 min_quote_notional=notional, pair=pair, price_basis=basis)
            if entry is None:
                outcome.update(input_status="ENTRY_PRICE_UNAVAILABLE", price_basis=PRICE_BASIS_UNAVAILABLE)
                row.outcome_json = outcome
                continue
            features = _features(events, _utc(row.snapshot_at))
            scoring = score_features(features)
            row.alpha_score, row.scored_at = scoring["alpha_score"], now
            row.coverage_generation = entry_proof["generation"]
            row.features_json = {k: str(v) if isinstance(v, Decimal) else v for k, v in features.items()}
            row.features_json["risk_status"] = scoring["risk_status"]
            row.components_json = {k: str(v) for k, v in scoring["components"].items()}
            row.components_json["available_points"] = scoring["available_points"]
            row.positive_reasons, row.negative_reasons = scoring["positive_reasons"], scoring["negative_reasons"]
            row.warnings = scoring["warnings"]
            row.entry_vwap = entry
            row.input_manifest = input_manifest(
                session, pair=pair, trades=events, proof=entry_proof,
                start=row.pair_created_at, end=row.snapshot_at, now=now,
                minimum_quote=notional, basis=basis, entry=entry,
            )
            outcome.update(price_basis=basis, entry_vwap_exact=str(entry), input_status="VERIFIED")
            row.task_status = "TRACKING"
            scored += 1
        _evaluate_outcome(session, row, pair, outcome, now=now, minimum_quote=notional)
    session.flush()
    metrics.observe("alpha_iteration_seconds", perf_counter() - started)
    metrics.observe("alpha_snapshots_examined", len(rows))
    metrics.increment("alpha_snapshots_scored", scored)
    return scored


def _trades(session, row, *, start, end):
    return session.scalars(select(TradeEvent).where(
        TradeEvent.chain == "bsc", TradeEvent.pair_address == row.pair_address,
        TradeEvent.token_address == row.token_address, TradeEvent.timestamp >= start,
        TradeEvent.timestamp <= end, *canonical_fact_conditions(TradeEvent),
    ).order_by(TradeEvent.timestamp, TradeEvent.id)).all()


def _evaluate_outcome(session, row, pair, outcome, *, now, minimum_quote):
    entry = _decimal(outcome.get("entry_vwap_exact"))
    basis = outcome.get("price_basis")
    if entry is None or entry <= 0 or basis not in {PRICE_BASIS_HUMAN, PRICE_BASIS_RAW}:
        row.outcome_json = outcome
        return
    target = _utc(row.snapshot_at) + TRACKING_HORIZON
    end = min(now, target)
    proof = coverage_proof(session, pair, start=row.snapshot_at, end=target, as_of=now)
    complete = now >= target and proof["status"] == "VERIFIED"
    events = [e for e in _trades(session, row, start=row.snapshot_at, end=end)
              if _utc(e.timestamp) > _utc(row.snapshot_at)]
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
    outcome.update(future_coverage=proof, tracking_complete=complete,
                   evaluation_manifest=input_manifest(
                       session, pair=pair, trades=events, proof=proof, start=row.snapshot_at,
                       end=end, now=now, minimum_quote=minimum_quote, basis=basis,
                       entry=entry, entry_digest=row.input_manifest["digest"],
                   ),
                   **{f"reached_{n}x": True if row.max_realisable_return is not None
                      and row.max_realisable_return >= n - 1 else (False if complete else None)
                      for n in (2, 3, 5, 10)})
    row.outcome_json = outcome
    if complete:
        row.task_status, row.next_evaluation_at = "COMPLETE", None

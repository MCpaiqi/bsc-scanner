"""Forward-performance observation and point-in-time horizon evaluation."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from statistics import median
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from smms.db.models import (
    ForwardEvaluationCursor,
    LiveSignalObservation,
    LiveSignalOutcome,
    RadarCandidate,
    Signal,
    Token,
)
from smms.evaluation import (
    ENDPOINT_TOLERANCE,
    FORWARD_EVALUATION_VERSION,
    evaluate_price_path,
    positive_price,
)
from smms.radar.forward_prices import load_usd_prices

HORIZONS = (("30s", 30), ("1m", 60), ("5m", 300), ("15m", 900), ("30m", 1800), ("1h", 3600), ("4h", 14400), ("24h", 86400))
OBSERVED_CLASSES = {"WATCH", "INTERESTING", "HIGH_SIGNAL", "REJECTED", "MARKET_BASELINE"}
ENTRY_MAX_AGE = timedelta(seconds=30)
RETRY_INTERVAL = timedelta(seconds=60)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _decimal(value: Any) -> Decimal | None:
    try:
        result = None if value is None or str(value).upper() == "UNKNOWN" else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result is not None and result.is_finite() else None


def _sample_rejected(token: str, observed_at: datetime, rate: int = 10) -> bool:
    """Stable 10% sample, independent of query order or future outcomes."""
    key = f"{token.lower()}|{_utc(observed_at).isoformat()}".encode()
    return int(hashlib.sha256(key).hexdigest()[:8], 16) % 100 < rate


def record_observation(
    session: Session,
    candidate: RadarCandidate,
    signal_class: str,
    *,
    at: datetime,
    reason: str = "SIGNAL_OR_STATE_TRANSITION",
) -> LiveSignalObservation | None:
    """Persist one immutable T0 row for a first class entry."""
    if signal_class not in OBSERVED_CLASSES:
        return None
    token = session.get(Token, candidate.token_id)
    if token is None:
        return None
    existing = session.scalar(select(LiveSignalObservation).where(
        LiveSignalObservation.candidate_id == candidate.id,
        LiveSignalObservation.signal_class == signal_class,
    ))
    if existing:
        return existing
    signal = session.get(Signal, candidate.latest_signal_id) if candidate.latest_signal_id else None
    features = dict(candidate.features_json or {})
    market_cap = _decimal(features.get("market_cap")) or (signal.market_cap if signal else None)
    liquidity = _decimal(features.get("liquidity")) or (signal.entry_liquidity if signal else None)
    observed_at = _utc(at)
    sample_group = signal_class
    if signal_class == "MARKET_BASELINE" and not _sample_rejected(
        f"{token.chain}:{token.address}", observed_at, rate=10
    ):
        return None
    if signal_class == "REJECTED" and not _sample_rejected(token.address, observed_at):
        sample_group = "REJECTED_UNSAMPLED"
    entries = load_usd_prices(
        session, [(token.address, observed_at - ENTRY_MAX_AGE, observed_at)], as_of=observed_at,
    ).get(token.address, [])
    latest_time = max((p.timestamp for p in entries), default=None)
    entry = min((p for p in entries if p.timestamp == latest_time),
                key=lambda p: p.reference, default=None)
    price = entry.price if entry else None
    entry_source = dict(entry.provenance) if entry else None
    snapshot = {
        "token": token.address,
        "observed_at": observed_at.isoformat(),
        "signal_class": signal_class,
        "signal_score": str(signal.signal_strength) if signal and signal.signal_strength is not None else None,
        "price": str(price) if price is not None else None,
        "entry_price_exact": str(price) if price is not None else None,
        "price_basis": "USD" if entry else "UNKNOWN",
        "entry_source": entry_source,
        "entry_max_age_seconds": int(ENTRY_MAX_AGE.total_seconds()),
        "pair_address": entry_source["pair_address"] if entry_source else None,
        "reason": reason,
        "market_cap": str(market_cap) if market_cap is not None else None,
        "liquidity": str(liquidity) if liquidity is not None else None,
        "smart_money_snapshot_id": candidate.smart_snapshot_id,
        "risk_snapshot_id": candidate.risk_snapshot_id,
        "feature_version": features.get("feature_version", "radar-features-v1"),
        "config_version": signal.config_version if signal else "config-v1",
        "model_version": signal.model_version if signal else "signal-v1",
    }
    row = LiveSignalObservation(
        candidate_id=candidate.id,
        token_id=token.id,
        chain=token.chain,
        token_address=token.address,
        observed_at=observed_at,
        signal_class=signal_class,
        signal_score=signal.signal_strength if signal else None,
        price=price,
        market_cap=market_cap,
        liquidity=liquidity,
        smart_money_snapshot_id=candidate.smart_snapshot_id,
        risk_snapshot_id=candidate.risk_snapshot_id,
        feature_version=str(features.get("feature_version", "radar-features-v1")),
        config_version=signal.config_version if signal else "config-v1",
        model_version=signal.model_version if signal else "signal-v1",
        sample_group=sample_group,
        t0_snapshot_json=snapshot,
    )
    try:
        with session.begin_nested():
            session.add(row)
            session.flush()
            session.add_all(_new_outcomes(row))
            session.flush()
    except IntegrityError:
        return session.scalar(select(LiveSignalObservation).where(
            LiveSignalObservation.candidate_id == candidate.id,
            LiveSignalObservation.signal_class == signal_class,
        ))
    return row


def _new_outcomes(observation: LiveSignalObservation) -> list[LiveSignalOutcome]:
    return [LiveSignalOutcome(
        observation_id=observation.id, horizon=horizon,
        target_at=_utc(observation.observed_at) + timedelta(seconds=seconds),
        next_evaluation_at=_utc(observation.observed_at) + timedelta(seconds=seconds),
        evaluation_version=FORWARD_EVALUATION_VERSION,
    ) for horizon, seconds in HORIZONS]


def _initialize_outcomes(session: Session, *, batch_size: int) -> None:
    """Repair a bounded observation slice; wrap to catch late lower-ID commits.

    New production observations enqueue atomically. This sweep repairs legacy
    imports and partial backfills without assuming ID order equals commit order.
    """
    if session.get_bind().dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    session.execute(insert(ForwardEvaluationCursor).values(
        evaluation_version=FORWARD_EVALUATION_VERSION, last_observation_id=0,
    ).on_conflict_do_nothing(index_elements=["evaluation_version"]))
    cursor = session.scalar(select(ForwardEvaluationCursor).where(
        ForwardEvaluationCursor.evaluation_version == FORWARD_EVALUATION_VERSION,
    ).with_for_update())
    observations = session.scalars(select(LiveSignalObservation).where(
        LiveSignalObservation.id > cursor.last_observation_id, LiveSignalObservation.chain == "bsc",
    ).order_by(LiveSignalObservation.id).limit(batch_size)).all()
    if not observations:
        cursor.last_observation_id = 0
        return
    existing = {(row.observation_id, row.horizon) for row in session.scalars(
        select(LiveSignalOutcome).where(
            LiveSignalOutcome.observation_id.in_([o.id for o in observations]),
            LiveSignalOutcome.evaluation_version == FORWARD_EVALUATION_VERSION,
        )
    )}
    for observation in observations:
        session.add_all([row for row in _new_outcomes(observation)
                         if (row.observation_id, row.horizon) not in existing])
    cursor.last_observation_id = observations[-1].id
    session.flush()


def evaluate_due(session: Session, *, at: datetime | None = None, batch_size: int = 256) -> int:
    """Evaluate bounded, persistent due work; missing evidence remains retryable."""
    if not 1 <= batch_size <= 2000:
        raise ValueError("batch_size must be between 1 and 2000")
    now = _utc(at or datetime.now(UTC))
    _initialize_outcomes(session, batch_size=batch_size)
    due = session.scalars(select(LiveSignalOutcome).where(
        LiveSignalOutcome.evaluation_version == FORWARD_EVALUATION_VERSION,
        LiveSignalOutcome.status == "PENDING", LiveSignalOutcome.target_at <= now,
        LiveSignalOutcome.next_evaluation_at <= now,
    ).order_by(LiveSignalOutcome.next_evaluation_at, LiveSignalOutcome.id)
        .limit(batch_size).with_for_update(skip_locked=True)).all()
    if not due:
        return 0
    observations = {row.id: row for row in session.scalars(select(LiveSignalObservation).where(
        LiveSignalObservation.id.in_({item.observation_id for item in due}),
    ))}
    ranges = [(observations[item.observation_id].token_address,
               _utc(observations[item.observation_id].observed_at),
               _utc(item.target_at) + ENDPOINT_TOLERANCE) for item in due]
    prices = load_usd_prices(session, ranges, as_of=now)
    changed = 0
    for outcome in due:
        observation = observations[outcome.observation_id]
        entry = observation.t0_snapshot_json or {}
        outcome.evaluated_at = now
        outcome.next_evaluation_at = now + RETRY_INTERVAL
        outcome.endpoint_tolerance_seconds = int(ENDPOINT_TOLERANCE.total_seconds())
        if entry.get("price_basis") != "USD":
            outcome.error_code = "PRICE_BASIS_UNKNOWN"
            continue
        t0_price = positive_price(entry.get("entry_price_exact") or entry.get("price")
                                  or observation.price)
        if t0_price is None:
            outcome.error_code = "MISSING_ENTRY_PRICE"
            continue
        pair = entry.get("pair_address")
        points = [p for p in prices.get(observation.token_address, [])
                  if not pair or p.provenance["pair_address"] == pair]
        result = evaluate_price_path(
            t0_price, observation.observed_at, points,
            horizon=_utc(outcome.target_at) - _utc(observation.observed_at), at=now,
        )
        if result is None:
            outcome.error_code = "MISSING_PRICE"
            continue
        liquidity = [value for p in result.path if p.provenance
                     and (value := _decimal(p.provenance.get("liquidity_usd"))) is not None]
        liquidity_failure = (any(value <= observation.liquidity * Decimal("0.10")
                                 for value in liquidity)
                             if observation.liquidity is not None and liquidity else None)
        digest = hashlib.sha256()
        for point in result.path:
            digest.update(json.dumps([point.timestamp.isoformat(), str(point.price), point.reference],
                                     separators=(",", ":")).encode())
        outcome.return_pct = result.return_pct
        outcome.max_return_pct = result.high_return
        outcome.max_drawdown_pct = result.max_drawdown
        outcome.target_hit = result.high_return >= 1
        outcome.stop_hit = result.low_return <= Decimal("-0.50")
        outcome.liquidity_failure = liquidity_failure
        outcome.rug = bool(liquidity_failure or result.low_return <= Decimal("-0.90"))
        outcome.endpoint_price = result.endpoint.price
        outcome.endpoint_at = result.endpoint.timestamp
        outcome.endpoint_offset_seconds = Decimal(str(result.endpoint_offset_seconds))
        outcome.provenance_json = {
            "price_basis": "USD", "entry": entry.get("entry_source"),
            "entry_price_exact": str(t0_price), "endpoint": dict(result.endpoint.provenance),
            "return_pct_exact": str(result.return_pct),
            "max_return_pct_exact": str(result.high_return),
            "max_drawdown_pct_exact": str(result.max_drawdown),
            "path_count": len(result.path), "path_digest": digest.hexdigest(),
            "path_semantics": "OBSERVED_PRICE_PATH", "stop_basis": "ENTRY_RETURN",
        }
        outcome.status = "COMPLETE"
        outcome.error_code = None
        changed += 1
    session.flush()
    return changed


def performance(
    session: Session, *, at: datetime | None = None, evaluate: bool = False
) -> dict[str, Any]:
    if evaluate:
        evaluate_due(session, at=at)
    cutoff = _utc(at or datetime.now(UTC))
    rows = session.scalars(select(LiveSignalObservation).where(
        LiveSignalObservation.observed_at <= cutoff,
    )).all()
    observations_by_id = {row.id: row for row in rows}
    outcomes = session.scalars(select(LiveSignalOutcome).where(
        LiveSignalOutcome.status == "COMPLETE",
        LiveSignalOutcome.evaluation_version == FORWARD_EVALUATION_VERSION,
        LiveSignalOutcome.evaluated_at <= cutoff,
    )).all()
    groups = {"HIGH_SIGNAL": [], "WATCH": [], "REJECTED": [], "INTERESTING": []}
    for outcome in outcomes:
        observation = observations_by_id.get(outcome.observation_id)
        if observation and observation.sample_group in groups:
            groups[observation.sample_group].append(outcome)
    groups["MARKET_BASELINE"] = [
        outcome
        for outcome in outcomes
        if observations_by_id.get(outcome.observation_id)
        and observations_by_id[outcome.observation_id].sample_group == "MARKET_BASELINE"
    ]
    def summary(items: list[LiveSignalOutcome]) -> dict[str, Any]:
        returns = sorted(float(x.return_pct) for x in items if x.return_pct is not None)
        return {"sample_count": len(items), "median_return": median(returns) if returns else None,
                "win_rate": (sum(r > 0 for r in returns) / len(returns)) if returns else None,
                "2x_rate": (sum(bool(x.target_hit) for x in items) / len(items)) if items else None,
                "rug_rate": (sum(bool(x.rug) for x in items) / len(items)) if items else None,
                "max_drawdown": min((float(x.max_drawdown_pct) for x in items if x.max_drawdown_pct is not None), default=None)}
    return {"mode": "FORWARD_OBSERVATION", "evaluation_version": FORWARD_EVALUATION_VERSION,
            "as_of": cutoff.isoformat(),
            "endpoint_tolerance_seconds": int(ENDPOINT_TOLERANCE.total_seconds()),
            "path_semantics": "OBSERVED_PRICE_PATH",
            "horizons": {h: {group: summary([x for x in items if x.horizon == h])
                             for group, items in groups.items()} for h, _ in HORIZONS}}

"""Forward-performance observation and point-in-time horizon evaluation."""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from smms.chains.constants import BSC_CHAIN_ID
from smms.db.models import (
    LiveSignalObservation,
    LiveSignalOutcome,
    MarketEvent,
    RadarCandidate,
    Signal,
    Token,
)

HORIZONS = (("30s", 30), ("1m", 60), ("5m", 300), ("15m", 900), ("30m", 1800), ("1h", 3600), ("4h", 14400), ("24h", 86400))
OBSERVED_CLASSES = {"WATCH", "INTERESTING", "HIGH_SIGNAL", "REJECTED", "MARKET_BASELINE"}


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _decimal(value: Any) -> Decimal | None:
    try:
        return None if value is None or str(value).upper() == "UNKNOWN" else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


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
    price = _decimal(features.get("price")) or (signal.entry_price if signal else None)
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
    snapshot = {
        "token": token.address,
        "observed_at": observed_at.isoformat(),
        "signal_class": signal_class,
        "signal_score": str(signal.signal_strength) if signal and signal.signal_strength is not None else None,
        "price": str(price) if price is not None else None,
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
    session.add(row)
    try:
        with session.begin_nested():
            session.flush()
    except IntegrityError:
        return session.scalar(select(LiveSignalObservation).where(
            LiveSignalObservation.candidate_id == candidate.id,
            LiveSignalObservation.signal_class == signal_class,
        ))
    return row


def _prices(session: Session, observation: LiveSignalObservation, end: datetime) -> list[MarketEvent]:
    return list(session.scalars(select(MarketEvent).where(
        MarketEvent.chain_id == BSC_CHAIN_ID
        if observation.chain == "bsc"
        else MarketEvent.chain_id.is_(None),
        MarketEvent.token_address == observation.token_address,
        MarketEvent.timestamp >= observation.observed_at,
        MarketEvent.timestamp <= end,
        MarketEvent.price.is_not(None),
    ).order_by(MarketEvent.timestamp, MarketEvent.id)).all())


def evaluate_due(session: Session, *, at: datetime | None = None) -> int:
    """Retry due horizons; rows remain PENDING when the provider has no price yet."""
    now = _utc(at or datetime.now(UTC))
    changed = 0
    for observation in session.scalars(select(LiveSignalObservation)).all():
        t0_price = observation.price
        if t0_price is None or t0_price <= 0:
            for horizon, seconds in HORIZONS:
                _ensure_pending(session, observation, horizon, observation.observed_at + timedelta(seconds=seconds))
            continue
        for horizon, seconds in HORIZONS:
            target_at = _utc(observation.observed_at) + timedelta(seconds=seconds)
            outcome = _ensure_pending(session, observation, horizon, target_at)
            if outcome.status == "COMPLETE" or target_at > now:
                continue
            points = _prices(session, observation, now)
            endpoint = next((p for p in points if _utc(p.timestamp) >= target_at), None)
            if endpoint is None:
                outcome.status = "PENDING"
                outcome.error_code = "MISSING_PRICE"
                continue
            window = [p for p in points if _utc(p.timestamp) <= _utc(endpoint.timestamp)]
            returns = [(_decimal(p.price) / t0_price) - 1 for p in window if _decimal(p.price) is not None]
            if not returns:
                continue
            endpoint_return = (_decimal(endpoint.price) / t0_price) - 1
            max_return = max(returns)
            max_drawdown = min((r - max(running for running in returns[:i + 1])) for i, r in enumerate(returns))
            liquidity_failure = any(_decimal(getattr(p, "liquidity", None)) is not None and observation.liquidity is not None and _decimal(getattr(p, "liquidity", None)) <= observation.liquidity * Decimal("0.10") for p in window)
            outcome.return_pct = endpoint_return
            outcome.max_return_pct = max_return
            outcome.max_drawdown_pct = max_drawdown
            outcome.target_hit = max_return >= 1
            outcome.stop_hit = max_drawdown <= Decimal("-0.50")
            outcome.liquidity_failure = liquidity_failure
            outcome.rug = liquidity_failure or endpoint_return <= Decimal("-0.90")
            outcome.endpoint_price = _decimal(endpoint.price)
            outcome.evaluated_at = now
            outcome.status = "COMPLETE"
            outcome.error_code = None
            changed += 1
    session.flush()
    return changed


def _ensure_pending(session: Session, observation: LiveSignalObservation, horizon: str, target_at: datetime) -> LiveSignalOutcome:
    row = session.scalar(select(LiveSignalOutcome).where(
        LiveSignalOutcome.observation_id == observation.id,
        LiveSignalOutcome.horizon == horizon,
    ))
    if row is None:
        row = LiveSignalOutcome(observation_id=observation.id, horizon=horizon, target_at=target_at, status="PENDING")
        session.add(row)
        session.flush()
    return row


def performance(
    session: Session, *, at: datetime | None = None, evaluate: bool = False
) -> dict[str, Any]:
    if evaluate:
        evaluate_due(session, at=at)
    rows = session.scalars(select(LiveSignalObservation)).all()
    observations_by_id = {row.id: row for row in rows}
    outcomes = session.scalars(select(LiveSignalOutcome).where(LiveSignalOutcome.status == "COMPLETE")).all()
    groups = {"HIGH_SIGNAL": [], "WATCH": [], "REJECTED": [], "INTERESTING": []}
    for outcome in outcomes:
        observation = next((item for item in rows if item.id == outcome.observation_id), None)
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
        return {"sample_count": len(items), "median_return": returns[len(returns) // 2] if returns else None,
                "win_rate": (sum(r > 0 for r in returns) / len(returns)) if returns else None,
                "2x_rate": (sum(bool(x.target_hit) for x in items) / len(items)) if items else None,
                "rug_rate": (sum(bool(x.rug) for x in items) / len(items)) if items else None,
                "max_drawdown": min((float(x.max_drawdown_pct) for x in items if x.max_drawdown_pct is not None), default=None)}
    return {"mode": "FORWARD_OBSERVATION", "horizons": {h: {group: summary([x for x in items if x.horizon == h]) for group, items in groups.items()} for h, _ in HORIZONS}}

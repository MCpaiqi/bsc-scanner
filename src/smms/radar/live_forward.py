"""Forward-performance observation and point-in-time horizon evaluation."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from statistics import median
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from smms.db.models import (
    ForwardEvaluationCursor,
    ForwardEvaluationJob,
    LiveSignalObservation,
    LiveSignalOutcome,
)
from smms.evaluation import (
    ENDPOINT_TOLERANCE,
    FORWARD_EVALUATION_VERSION,
    evaluate_price_path,
    positive_price,
)
from smms.radar.forward_prices import load_usd_prices
from smms.research.maintenance import try_repair_ownership
from smms.research.state import capture_epoch
from smms.research.visibility import is_current_snapshot

HORIZONS = (("30s", 30), ("1m", 60), ("5m", 300), ("15m", 900), ("30m", 1800), ("1h", 3600), ("4h", 14400), ("24h", 86400))
OBSERVED_CLASSES = {"WATCH", "INTERESTING", "HIGH_SIGNAL", "REJECTED", "MARKET_BASELINE"}
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


def record_observation(session, candidate, signal_class, *, at, reason="SIGNAL_OR_STATE_TRANSITION"):
    """Record the first observed cohort; corrections retain its original T0."""
    if signal_class not in OBSERVED_CLASSES:
        return None
    from .forward_research import publish_observation, rebuild_observation

    existing = session.scalar(select(LiveSignalObservation).where(
        LiveSignalObservation.candidate_id == candidate.id, LiveSignalObservation.signal_class == signal_class,
    ).order_by(LiveSignalObservation.research_revision.desc()).limit(1)
        .execution_options(include_invalid_snapshots=True, populate_existing=True))
    if existing is not None:
        if is_current_snapshot(session, existing):
            return existing
        if rebuild_observation(session, existing):
            return None
        return session.scalar(select(LiveSignalObservation).where(
            LiveSignalObservation.candidate_id == candidate.id, LiveSignalObservation.signal_class == signal_class,
        ).execution_options(populate_existing=True))
    return publish_observation(session, candidate, signal_class, at=at, reason=reason)


def enqueue_horizons(session, observation):
    from .forward_research import root_id

    if observation.chain != "bsc" or not is_current_snapshot(session, observation):
        return
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    insert = pg_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert
    root = observation.id if observation.research_revision == 0 else root_id(session, observation)
    values = [{"observation_id": observation.id, "observation_root_id": root, "horizon": name,
               "target_at": _utc(observation.observed_at) + timedelta(seconds=seconds),
               "next_evaluation_at": _utc(observation.observed_at) + timedelta(seconds=seconds),
               "evaluation_version": FORWARD_EVALUATION_VERSION, "status": "PENDING", "attempts": 0}
              for name, seconds in HORIZONS]
    session.execute(insert(ForwardEvaluationJob).values(values).on_conflict_do_nothing(
        index_elements=["observation_id", "horizon", "evaluation_version"]))


def _initialize_outcomes(session, *, batch_size, now=None):
    from smms.research.state import insert_once
    from smms.research.visibility import visible_conditions

    if not try_repair_ownership(session, "forward-work", FORWARD_EVALUATION_VERSION):
        return
    cursor_query = select(ForwardEvaluationCursor).where(
        ForwardEvaluationCursor.evaluation_version == FORWARD_EVALUATION_VERSION,
    ).with_for_update(skip_locked=True).execution_options(populate_existing=True)
    cursor = session.scalar(cursor_query)
    if cursor is None:
        exists = session.scalar(select(ForwardEvaluationCursor.evaluation_version).where(
            ForwardEvaluationCursor.evaluation_version == FORWARD_EVALUATION_VERSION))
        if exists is not None:
            return  # Another worker owns repair; independent due claims proceed.
        insert_once(session, ForwardEvaluationCursor, {"evaluation_version": FORWARD_EVALUATION_VERSION,
                                                      "last_observation_id": 0}, ["evaluation_version"])
        cursor = session.scalar(cursor_query)
        if cursor is None:
            return
    observations = session.scalars(select(LiveSignalObservation).where(
        LiveSignalObservation.id > cursor.last_observation_id, LiveSignalObservation.chain == "bsc",
    ).order_by(LiveSignalObservation.id).limit(batch_size).execution_options(populate_existing=True)).all()
    for observation in observations:
        enqueue_horizons(session, observation)
    cursor.last_observation_id = observations[-1].id if observations else 0
    current = select(LiveSignalObservation.id).where(LiveSignalObservation.id == ForwardEvaluationJob.observation_id).exists()
    stale = session.scalars(select(ForwardEvaluationJob).where(
        ForwardEvaluationJob.status.in_(("PENDING", "COMPLETE")), ~current,
    ).order_by(ForwardEvaluationJob.id).limit(batch_size).with_for_update(skip_locked=True)).all()
    for job in stale:
        job.status, job.error_code = "SUPERSEDED", "OBSERVATION_INVALIDATED"
    # A result may be replaced by canonical repair while its observation stays
    # valid. Reconcile operational pointers under job-first lock ordering, never
    # by taking job locks inside result publication (which would deadlock workers).
    valid_result = select(LiveSignalOutcome.id).where(
        LiveSignalOutcome.id == ForwardEvaluationJob.result_id,
        LiveSignalOutcome.status == "COMPLETE", *visible_conditions(LiveSignalOutcome),
    ).exists()
    broken = session.scalars(select(ForwardEvaluationJob).where(
        ForwardEvaluationJob.status == "COMPLETE", current, ~valid_result,
    ).order_by(ForwardEvaluationJob.id).limit(batch_size).with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)).all()
    if broken:
        epoch, repaired_at = capture_epoch(session), _utc(now or datetime.now(UTC))
        latest = {(row.observation_id, row.horizon): row for row in session.scalars(select(LiveSignalOutcome).where(
            LiveSignalOutcome.observation_id.in_({job.observation_id for job in broken}),
            LiveSignalOutcome.evaluation_version == FORWARD_EVALUATION_VERSION,
            LiveSignalOutcome.source_epoch == epoch, LiveSignalOutcome.snapshot_status.in_(("VALID", "UNAVAILABLE")),
        ).execution_options(include_invalid_snapshots=True, populate_existing=True))}
        for job in broken:
            result = latest.get((job.observation_id, job.horizon))
            job.result_id = result.id if result else None
            if result is not None and result.snapshot_status == "VALID" and result.status == "COMPLETE":
                job.status, job.error_code = "COMPLETE", None
            else:
                job.status, job.error_code = "PENDING", result.error_code if result else "RESULT_INVALIDATED"
                job.next_evaluation_at = max(repaired_at, _utc(job.target_at))
    session.flush()


def evaluate_observation(session, observation, *, root, horizon, at, points, lineage, epoch):
    from smms.research.lineage import manifest
    from smms.research.publication import publish_snapshot

    from .forward_prices import price_dependencies, price_facts
    from .forward_research import reference

    at, start = _utc(at), _utc(observation.observed_at)
    seconds = dict(HORIZONS)[horizon]
    target = start + timedelta(seconds=seconds)
    entry = dict(observation.t0_snapshot_json or {})
    pair = entry.get("pair_address")
    points = [p for p in points if pair and p.provenance["pair_address"] == pair
              and start < p.timestamp <= min(at, target + ENDPOINT_TOLERANCE)]
    error = None
    if observation.snapshot_status != "VALID":
        error = "OBSERVATION_WITHDRAWN"
    elif entry.get("price_basis") != "USD":
        error = "PRICE_BASIS_UNKNOWN"
    elif not pair:
        error = "ENTRY_PAIR_UNKNOWN"
    entry_price = positive_price(entry.get("entry_price_exact") or entry.get("price") or observation.price)
    if error is None and entry_price is None:
        error = "MISSING_ENTRY_PRICE"
    result = evaluate_price_path(entry_price, start, points, horizon=timedelta(seconds=seconds), at=at) if error is None else None
    if result is None and error is None:
        error = "MISSING_PRICE"
    values, provenance = {}, {"price_basis": entry.get("price_basis", "UNKNOWN"), "entry": entry.get("entry_source")}
    if result is not None:
        liquidity = [value for p in result.path if p.provenance
                     and (value := _decimal(p.provenance.get("liquidity_usd"))) is not None]
        failure = (any(value <= observation.liquidity * Decimal("0.10") for value in liquidity)
                   if observation.liquidity is not None and liquidity else None)
        path_digest = hashlib.sha256()
        for point in result.path:
            path_digest.update(json.dumps([point.timestamp.isoformat(), str(point.price), point.reference],
                                          separators=(",", ":")).encode())
        values = {"return_pct": result.return_pct, "max_return_pct": result.high_return,
                  "max_drawdown_pct": result.max_drawdown, "target_hit": result.high_return >= 1,
                  "stop_hit": result.low_return <= Decimal("-0.50"), "liquidity_failure": failure,
                  "rug": bool(failure or result.low_return <= Decimal("-0.90")), "endpoint_price": result.endpoint.price,
                  "endpoint_at": result.endpoint.timestamp, "endpoint_offset_seconds": Decimal(str(result.endpoint_offset_seconds))}
        provenance.update(entry_price_exact=str(entry_price), endpoint=dict(result.endpoint.provenance),
                          return_pct_exact=str(result.return_pct), max_return_pct_exact=str(result.high_return),
                          max_drawdown_pct_exact=str(result.max_drawdown), path_count=len(result.path),
                          path_digest=path_digest.hexdigest(), path_semantics="OBSERVED_PRICE_PATH", stop_basis="ENTRY_RETURN")
    if capture_epoch(session, observation.chain) != epoch:
        raise ValueError("canonical epoch changed during Forward evaluation")
    return publish_snapshot(session, LiveSignalOutcome(
        chain=observation.chain, observation_id=observation.id, observation_root_id=root, horizon=horizon,
        target_at=target, evaluation_version=FORWARD_EVALUATION_VERSION, next_evaluation_at=at + RETRY_INTERVAL,
        evaluated_at=at, endpoint_tolerance_seconds=int(ENDPOINT_TOLERANCE.total_seconds()),
        status="COMPLETE" if result is not None else "UNKNOWN", error_code=error, provenance_json=provenance,
        source_epoch=epoch, snapshot_status="VALID" if result is not None else "UNAVAILABLE", **values,
        input_manifest=manifest(kind="live_signal_outcomes", parameters={
            "recipe": FORWARD_EVALUATION_VERSION, "observation_root_id": root, "observation_id": observation.id,
            "horizon": horizon, "target_at": target, "evaluation_as_of": at,
            "endpoint_tolerance_seconds": int(ENDPOINT_TOLERANCE.total_seconds()),
        }, facts=price_facts(lineage, points), dependencies=[reference(observation), *price_dependencies(lineage, points)],
            notes=[error] if error else []),
    ))


def rebuild_outcome(session, old):
    from smms.research.dependencies import (
        DependenciesPending,
        current_revision,
        require_rebuilt_dependencies,
    )

    if old.evaluation_version != FORWARD_EVALUATION_VERSION:
        return "UNSUPPORTED_VERSION"
    require_rebuilt_dependencies(session, old)
    source = session.scalar(select(LiveSignalObservation).where(LiveSignalObservation.id == old.observation_root_id)
                            .execution_options(include_invalid_snapshots=True))
    current = current_revision(session, source) if source else None
    if current is None:
        raise DependenciesPending("Forward observation is rebuilding")
    at, epoch, lineage = _utc(old.evaluated_at or old.target_at), capture_epoch(session), {}
    prices = load_usd_prices(session, [(current.token_address, _utc(current.observed_at), _utc(old.target_at) + ENDPOINT_TOLERANCE)],
                             as_of=at, lineage=lineage).get(current.token_address, [])
    evaluate_observation(session, current, root=old.observation_root_id, horizon=old.horizon, at=at,
                         points=prices, lineage=lineage, epoch=epoch)
    return None


def evaluate_due(session, *, at=None, batch_size=256):
    """Claim bounded retry jobs and append immutable results under source fences."""
    if not 1 <= batch_size <= 2000:
        raise ValueError("batch_size must be between 1 and 2000")
    now, epoch = _utc(at or datetime.now(UTC)), capture_epoch(session)
    _initialize_outcomes(session, batch_size=batch_size, now=now)
    due = session.scalars(select(ForwardEvaluationJob).join(
        LiveSignalObservation, LiveSignalObservation.id == ForwardEvaluationJob.observation_id,
    ).where(ForwardEvaluationJob.evaluation_version == FORWARD_EVALUATION_VERSION,
            ForwardEvaluationJob.status == "PENDING", ForwardEvaluationJob.target_at <= now,
            ForwardEvaluationJob.next_evaluation_at <= now)
        .order_by(ForwardEvaluationJob.next_evaluation_at, ForwardEvaluationJob.id).limit(batch_size)
        .with_for_update(skip_locked=True, of=ForwardEvaluationJob).execution_options(populate_existing=True)).all()
    if not due:
        return 0
    observations = {row.id: row for row in session.scalars(select(LiveSignalObservation).where(
        LiveSignalObservation.id.in_({job.observation_id for job in due}),
    ).execution_options(populate_existing=True))}
    results = {(row.observation_id, row.horizon): row for row in session.scalars(select(LiveSignalOutcome).where(
        LiveSignalOutcome.observation_id.in_(observations), LiveSignalOutcome.status == "COMPLETE",
        LiveSignalOutcome.evaluation_version == FORWARD_EVALUATION_VERSION,
    ).execution_options(populate_existing=True))}
    lineage = {}
    prices = load_usd_prices(session, [(observations[job.observation_id].token_address,
                                      _utc(observations[job.observation_id].observed_at),
                                      _utc(job.target_at) + ENDPOINT_TOLERANCE) for job in due if job.observation_id in observations],
                             as_of=now, lineage=lineage)
    changed = 0
    for job in due:
        observation = observations.get(job.observation_id)
        if observation is None:
            job.status, job.error_code = "SUPERSEDED", "OBSERVATION_INVALIDATED"
            continue
        current = results.get((observation.id, job.horizon))
        if current is not None:
            job.status, job.result_id, job.error_code = "COMPLETE", current.id, None
            continue
        job.attempts += 1
        job.evaluated_at, job.next_evaluation_at = now, now + RETRY_INTERVAL
        try:
            with session.begin_nested():
                if positive_price((observation.t0_snapshot_json or {}).get("entry_price_exact") or observation.price) is None:
                    from .forward_research import rebuild_observation

                    rebuild_observation(session, observation)
                    if not is_current_snapshot(session, observation):
                        job.status, job.error_code = "SUPERSEDED", "ENTRY_RECONSTRUCTED"
                        continue
                result = evaluate_observation(session, observation, root=job.observation_root_id, horizon=job.horizon,
                                              at=now, points=prices.get(observation.token_address, []), lineage=lineage, epoch=epoch)
                job.result_id, job.error_code = result.id, result.error_code
                if is_current_snapshot(session, result) and result.status == "COMPLETE":
                    job.status = "COMPLETE"
                    changed += 1
        except (ArithmeticError, ValueError, SQLAlchemyError) as exc:
            job.error_code = type(exc).__name__
    session.flush()
    return changed


def performance(
    session: Session, *, at: datetime | None = None, evaluate: bool = False
) -> dict[str, Any]:
    if evaluate:
        evaluate_due(session, at=at)
    cutoff = _utc(at or datetime.now(UTC))
    epoch = capture_epoch(session)
    # One statement sees matching input/result revisions. A later source
    # transaction cannot splice old cohorts into freshly corrected results.
    pairs = session.execute(select(LiveSignalObservation, LiveSignalOutcome).outerjoin(
        LiveSignalOutcome, (LiveSignalOutcome.observation_id == LiveSignalObservation.id)
        & (LiveSignalOutcome.status == "COMPLETE")
        & (LiveSignalOutcome.evaluation_version == FORWARD_EVALUATION_VERSION)
        & (LiveSignalOutcome.evaluated_at <= cutoff),
    ).where(LiveSignalObservation.chain == "bsc", LiveSignalObservation.observed_at <= cutoff)
        .execution_options(populate_existing=True)).all()
    observations_by_id = {observation.id: observation for observation, _ in pairs}
    outcomes = [outcome for _, outcome in pairs if outcome is not None]
    withdrawn = session.scalars(select(LiveSignalObservation).where(
        LiveSignalObservation.chain == "bsc", LiveSignalObservation.source_epoch == epoch,
        LiveSignalObservation.snapshot_status == "UNAVAILABLE", LiveSignalObservation.observed_at <= cutoff,
    ).execution_options(include_invalid_snapshots=True, populate_existing=True)).all()
    withheld = session.scalar(select(func.count()).select_from(LiveSignalObservation).where(
        LiveSignalObservation.chain == "bsc", LiveSignalObservation.observed_at <= cutoff,
        LiveSignalObservation.snapshot_status.in_(("VALID", "UNAVAILABLE", "INVALIDATED")),
        or_(LiveSignalObservation.source_epoch < epoch, LiveSignalObservation.snapshot_status == "INVALIDATED"),
    ).execution_options(include_invalid_snapshots=True))
    current_epoch = capture_epoch(session)
    rebuilding = current_epoch != epoch
    if rebuilding:
        observations_by_id, outcomes, withdrawn = {}, [], []
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
    horizons = {}
    for name, seconds in HORIZONS:
        horizons[name] = {}
        for group, items in groups.items():
            cohort = [row for row in observations_by_id.values() if row.sample_group == group]
            completed = [item for item in items if item.horizon == name]
            due = sum(_utc(row.observed_at) + timedelta(seconds=seconds) <= cutoff for row in cohort)
            horizons[name][group] = summary(completed) | {
                "observed_count": len(cohort), "pending_count": due - len(completed),
                "not_due_count": len(cohort) - due,
                "withdrawn_count": sum(row.sample_group == group for row in withdrawn),
            }
    return {"mode": "FORWARD_OBSERVATION", "evaluation_version": FORWARD_EVALUATION_VERSION,
            "data_status": "REBUILDING" if rebuilding or withheld else "CURRENT",
            "source_epoch": epoch, "current_epoch": current_epoch,
            "cohort_policy": "recorded-transition-cohort-v1", "withdrawn_count": len(withdrawn),
            "withheld_count": None if rebuilding else withheld,
            "as_of": cutoff.isoformat(),
            "endpoint_tolerance_seconds": int(ENDPOINT_TOLERANCE.total_seconds()),
            "path_semantics": "OBSERVED_PRICE_PATH",
            "horizons": horizons}

"""Bounded persistent repair and canonical reconstruction of derived snapshots."""

from datetime import datetime, timedelta
from decimal import Decimal
from time import perf_counter
from types import SimpleNamespace

from sqlalchemy import or_, select
from sqlalchemy.exc import SQLAlchemyError

from smms.db.models import (
    BacktestDataset,
    EarlyBuyer,
    LiveSignalObservation,
    LiveSignalOutcome,
    RadarCandidate,
    RadarFeatureObservation,
    ResearchRebuildCursor,
    ResearchRebuildJob,
    Signal,
    TokenMarketSnapshot,
    TokenRiskSnapshot,
    TokenSmartMoneySnapshot,
    WalletSmartMoneySnapshot,
    WalletTokenPosition,
)
from smms.evaluation import utc

from .lineage import manifest
from .maintenance import bounded_lock_wait, try_repair_ownership
from .publication import IDENTITY, MODELS, lock_identity, publish_snapshot
from .state import capture_epoch, insert_once


def _repair(session, *, epoch, now, batch_size):
    for kind, model in MODELS.items():
        if not try_repair_ownership(session, "research-repair", kind):
            continue
        query = select(ResearchRebuildCursor).where(ResearchRebuildCursor.kind == kind).with_for_update(skip_locked=True)
        cursor = session.scalar(query.execution_options(populate_existing=True))
        if cursor is None:
            if session.scalar(select(ResearchRebuildCursor.kind).where(ResearchRebuildCursor.kind == kind)) is not None:
                continue
            insert_once(session, ResearchRebuildCursor, {"kind": kind, "last_id": 0}, ["kind"])
            cursor = session.scalar(query.execution_options(populate_existing=True))
            if cursor is None:
                continue
        rows = session.scalars(select(model).where(
            model.chain == "bsc", model.id > cursor.last_id,
            model.source_epoch >= 0,
            or_(model.source_epoch < epoch, model.snapshot_status == "INVALIDATED"),
            model.snapshot_status.in_(("VALID", "UNAVAILABLE", "INVALIDATED")),
        ).order_by(model.id).limit(batch_size)
            .execution_options(include_invalid_snapshots=True, populate_existing=True)).all()
        for row in rows:
            insert_once(session, ResearchRebuildJob, {
                "kind": kind, "snapshot_id": row.id, "requested_epoch": epoch,
                "status": "PENDING", "attempts": 0, "next_attempt_at": now,
            }, ["kind", "snapshot_id", "requested_epoch"])
        cursor.last_id = rows[-1].id if rows else 0
    session.flush()


def _rebuild(session, old):
    parameters = (old.input_manifest or {}).get("parameters", {})
    if isinstance(old, LiveSignalObservation):
        from smms.radar.forward_research import rebuild_observation

        return rebuild_observation(session, old)
    elif isinstance(old, LiveSignalOutcome):
        from smms.radar.live_forward import rebuild_outcome

        return rebuild_outcome(session, old)
    elif isinstance(old, BacktestDataset):
        from smms.backtest.dataset import HistoricalDatasetBuilder

        from .dependencies import require_rebuilt_dependencies

        require_rebuilt_dependencies(session, old)
        if parameters and parameters.get("model_version") != "dataset-v5":
            return "UNSUPPORTED_VERSION"
        HistoricalDatasetBuilder(
            session, chain=old.chain, model_version="dataset-v5",
            score_version=parameters.get("score_version", "v2"),
            risk_version=parameters.get("risk_version", "risk-v1"), config=parameters.get("config"),
            evaluation_as_of=datetime.fromisoformat(parameters["evaluation_as_of"]) if parameters.get("evaluation_as_of") else None,
        ).materialize(old.period_from, old.period_to, dataset_version=old.dataset_version, commit=False,
                      reconstruction_policy=parameters.get("reconstruction_policy") if parameters else "legacy-upgrade-to-dataset-v5")
    elif isinstance(old, (RadarFeatureObservation, Signal)):
        from smms.radar.config import RadarConfig
        from smms.radar.engine import RadarEngine
        from smms.radar.live_signal import LiveSignalService
        from smms.radar.projection import apply_projection, build_projection

        from .dependencies import require_rebuilt_dependencies

        require_rebuilt_dependencies(session, old)
        candidate_id = old.candidate_id if isinstance(old, RadarFeatureObservation) else parameters.get("candidate_id")
        candidate = session.get(RadarCandidate, candidate_id) if candidate_id else session.scalar(
            select(RadarCandidate).where(RadarCandidate.token_id == old.token_id))
        if candidate is None:
            return "CANDIDATE_DELETED"
        if isinstance(old, RadarFeatureObservation):
            engine = RadarEngine(session, RadarConfig(**parameters.get("config", {})))
            current = build_projection(engine, candidate, at=old.as_of, config_hash=old.config_hash,
                                       reconstruction_policy=parameters.get("reconstruction_policy", "legacy-defaults-v1" if not parameters else None))
            if candidate.projection_id == old.id:
                apply_projection(engine, candidate, current)
        else:
            if old.model_version != "signal-v1":
                return "UNSUPPORTED_VERSION"
            from smms.backtest.signal_engine import SignalWeights

            LiveSignalService(session, radar_config=RadarConfig(**parameters.get("radar_config", {})),
                              weights=SignalWeights.from_mapping(parameters.get("weights")),
                              threshold=parameters.get("threshold", 50),
                              config_version=parameters.get("config_version", "phase-7.9.4"),
                              reconstruction_policy=parameters.get("reconstruction_policy", "legacy-defaults-v1" if not parameters else None)).materialize(
                candidate, as_of=old.created_at,
            )
    elif isinstance(old, (EarlyBuyer, WalletTokenPosition)):
        from smms.smart_money.early_buyers import EarlyBuyerConfig
        from smms.smart_money.engine import EarlyBuyerEngine
        from smms.smart_money.replay import REPLAY_VERSION

        if parameters.get("recipe", REPLAY_VERSION) != REPLAY_VERSION:
            return "UNSUPPORTED_VERSION"
        config = parameters.get("config") or ({"window_type": old.window_type, "window_value": old.window_value}
                                               if isinstance(old, EarlyBuyer) else {})
        EarlyBuyerEngine(session, chain=old.chain).refresh(
            old.token_address, as_of=old.as_of, config=EarlyBuyerConfig(**config), recipe_hash=old.recipe_hash,
            reconstruction_policy=parameters.get("reconstruction_policy", "legacy-defaults-v1" if not parameters else None),
        )
    elif isinstance(old, (TokenSmartMoneySnapshot, WalletSmartMoneySnapshot)):
        from smms.smart_money.aggregation import (
            PROFILE_VERSION,
            SCORE_VERSION,
            TOKEN_INPUT_POLICY,
            WALLET_INPUT_POLICY,
            IntelligenceAggregationService,
        )
        from smms.smart_money.early_buyers import EarlyBuyerConfig

        if old.score_version != SCORE_VERSION or parameters.get("profile_version", PROFILE_VERSION) != PROFILE_VERSION:
            return "UNSUPPORTED_VERSION"
        allowed_policies = (None, "canonical-wallet-replay-v1", WALLET_INPUT_POLICY) if isinstance(old, WalletSmartMoneySnapshot) else (None, "canonical-research-v1", TOKEN_INPUT_POLICY)
        if parameters.get("input_policy") not in allowed_policies:
            return "UNSUPPORTED_INPUT_POLICY"
        service = IntelligenceAggregationService(
            session, chain=old.chain, score_version=old.score_version,
            profile_version=parameters.get("profile_version", PROFILE_VERSION),
            min_wallet_score=Decimal(str(parameters.get("min_wallet_score", 60))),
            early_buyer_config=EarlyBuyerConfig(**parameters.get("early_buyer_config", {})),
            reconstruction_policy=parameters.get("reconstruction_policy", "legacy-defaults-v1" if not parameters else None),
        )
        if isinstance(old, WalletSmartMoneySnapshot):
            if old.profile_version != PROFILE_VERSION:
                return "UNSUPPORTED_VERSION"
            service.refresh_wallet(old.wallet_address, as_of=old.as_of)
        else:
            service.refresh_token(old.token_address, as_of=old.as_of)
    elif isinstance(old, TokenRiskSnapshot):
        from smms.risk.aggregation import RiskAggregationService
        from smms.risk.engine import RISK_VERSION

        if old.risk_version != RISK_VERSION:
            return "UNSUPPORTED_VERSION"
        RiskAggregationService(session, chain=old.chain, risk_version=old.risk_version).refresh(
            as_of=old.as_of, token_address=old.token_address, historical_rebuild=True,
        )
    else:
        from smms.market.snapshots import rebuild_trade_snapshot

        if old.source != "ON_CHAIN_TRADE_AGGREGATION":
            return "UNSUPPORTED_SOURCE"
        read_epoch = capture_epoch(session, old.chain)
        result = rebuild_trade_snapshot(session, SimpleNamespace(
            chain=old.chain, chain_id=old.chain_id, token_address=old.token_address,
            pair_address=old.pair_address, timestamp=old.as_of, metadata_json={"dex": old.dex},
        ), as_of=old.as_of, source_epoch=read_epoch)
        if result is None:
            publish_snapshot(session, TokenMarketSnapshot(
                chain=old.chain, chain_id=old.chain_id, token_address=old.token_address,
                pair_address=old.pair_address, source=old.source, as_of=old.as_of,
                observed_at=old.observed_at, status="UNAVAILABLE", snapshot_status="UNAVAILABLE",
                source_epoch=read_epoch, metadata_json={"reason": "NO_CANONICAL_INPUT"},
                input_manifest=manifest(kind=old.__tablename__, parameters={
                    "as_of": old.as_of, "token_address": old.token_address,
                    "pair_address": old.pair_address, "source": old.source,
                }, facts={"trades": []}, notes=["NO_CANONICAL_INPUT"]),
            ))
    return None


def rebuild_due(session, *, now: datetime, batch_size: int = 16, report=None) -> int:
    if not 1 <= batch_size <= 256:
        raise ValueError("batch_size must be between 1 and 256")
    now = utc(now)
    started = perf_counter()
    publication_seconds = 0.0
    epoch = capture_epoch(session)
    _repair(session, epoch=epoch, now=now, batch_size=batch_size)
    repair_seconds = perf_counter() - started
    started = perf_counter()
    jobs = session.scalars(select(ResearchRebuildJob).where(
        ResearchRebuildJob.status == "PENDING", ResearchRebuildJob.next_attempt_at <= now,
    ).order_by(ResearchRebuildJob.next_attempt_at, ResearchRebuildJob.id).limit(batch_size)
        .with_for_update(skip_locked=True)).all()
    for job in jobs:
        if job.requested_epoch != epoch:
            job.status = "STALE_EPOCH"
            continue
        model = MODELS[job.kind]
        old = session.scalar(select(model).where(model.id == job.snapshot_id)
                             .execution_options(include_invalid_snapshots=True, populate_existing=True))
        if old is None or old.snapshot_status == "SUPERSEDED":
            job.status = "SUPERSEDED"
            continue
        job.attempts += 1
        try:
            with session.begin_nested(), bounded_lock_wait(session):
                locking = perf_counter()
                try:
                    with session.begin_nested() as probe:
                        root = lock_identity(session, model, {name: getattr(old, name) for name in IDENTITY[model]})
                        # Detect an already-busy root before reading facts, but
                        # do not retain its lock across expensive reconstruction.
                        # Final publication reacquires it and checks the winner.
                        probe.rollback()
                finally:
                    publication_seconds += perf_counter() - locking
                session.info["research_rebuild_root"] = root
                try:
                    unsupported = _rebuild(session, old)
                finally:
                    session.info.pop("research_rebuild_root", None)
                if unsupported:
                    job.status, job.last_error = "UNSUPPORTED", unsupported
                    continue
                latest = session.scalar(select(model).where(*[
                    getattr(model, name) == getattr(old, name) for name in IDENTITY[model]
                ]).order_by(model.research_revision.desc()).limit(1)
                    .execution_options(include_invalid_snapshots=True))
                current = capture_epoch(session)
                if latest is None or latest.source_epoch != current or latest.snapshot_status not in {"VALID", "UNAVAILABLE"}:
                    job.status = "STALE_EPOCH"
                else:
                    job.status, job.result_id, job.last_error = "COMPLETE", latest.id, None
        except (ArithmeticError, ValueError, SQLAlchemyError) as exc:
            job.status, job.last_error = "PENDING", type(exc).__name__
            job.next_attempt_at = now + timedelta(seconds=60)
    session.flush()
    if report is not None:
        report.update(repair_seconds=repair_seconds, publication_lock_seconds=publication_seconds,
                      rebuild_seconds=perf_counter() - started, jobs_examined=len(jobs),
                      **{key: sum(job.status == status for job in jobs) for key, status in (
                          ("jobs_completed", "COMPLETE"), ("jobs_deferred", "PENDING"),
                          ("jobs_unsupported", "UNSUPPORTED"), ("jobs_stale", "STALE_EPOCH"),
                          ("jobs_superseded", "SUPERSEDED"))})
    return len(jobs)

"""Canonical radar API; external feeds do not select this universe."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, func, select

from smms.alpha.live import SCORE_VERSION
from smms.alpha.policy import COHORT_SECONDS, HIGH_SCORE, LABEL_VERSION, policy_id
from smms.api.alpha_metadata import load_alpha_metadata
from smms.chains.constants import BSC_CHAIN_ID, validate_bsc_chain
from smms.config.settings import get_settings
from smms.db.database import session_scope
from smms.db.models import AlphaSnapshot, RadarCandidate, RuntimeWorkerStatus, StreamCheckpoint
from smms.db.redis import check_redis
from smms.observability import freshness_status, metrics, observability, source_health_registry
from smms.radar.domain import CandidateStatus
from smms.radar.events import event_bus, sse_encode
from smms.radar.live_forward import performance
from smms.radar.views import candidate_detail, candidates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/radar/candidates", tags=["radar"])
meta_router = APIRouter(prefix="/api/radar", tags=["radar"])
alpha_router = APIRouter(prefix="/api/alpha", tags=["alpha"])


def _utc_datetime(value: datetime) -> datetime:
    """Normalize database timestamps before calculating live age."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _manifest_summary(manifest):
    return {key: manifest.get(key) for key in ("schema_version", "digest", "captured_at", "entry_manifest_digest")} | {
        "trade_count": len(manifest.get("trade_ids", [])), "receipt_count": len(manifest.get("receipts", [])),
    } if manifest else {}


def _outcome_summary(outcome):
    result = dict(outcome or {})
    if "evaluation_manifest" in result:
        result["evaluation_manifest"] = _manifest_summary(result["evaluation_manifest"])
    if "invalidated_outcome" in result:
        result["invalidated_outcome"] = _outcome_summary(result["invalidated_outcome"])
    return result


def _alpha_payload(row: AlphaSnapshot, *, now: datetime, metadata: dict | None = None) -> dict[str, object]:
    current_age_seconds = max(0, int((now - _utc_datetime(row.pair_created_at)).total_seconds()))
    return {
        "snapshot_id": row.id,
        "identity": {"chain": row.chain, "token": row.token_address, "pair": row.pair_address,
                     **dict.fromkeys(("symbol", "name", "image_url", "quote_symbol", "dex", "dex_url")),
                     **(metadata or {})},
        "pair_created_at": row.pair_created_at, "snapshot_at": row.snapshot_at,
        "observation_seconds": row.observation_seconds,
        "current_age_seconds": current_age_seconds,
        "alpha_score": float(row.alpha_score) if row.alpha_score is not None else None,
        "task_status": row.task_status,
        "input_revision": row.input_revision, "supersedes_id": row.supersedes_id,
        "input_manifest": _manifest_summary(row.input_manifest),
        "lineage_url": f"/api/alpha/revisions/{row.id}/lineage",
        "outcome_validity": "INVALIDATED" if row.task_status == "INVALIDATED" else row.task_status,
        "score_version": row.score_version, "status": "EXPERIMENTAL ALPHA / SHADOW MODE",
        "components": row.components_json, "features": row.features_json,
        "positive_reasons": row.positive_reasons, "negative_reasons": row.negative_reasons,
        "warnings": row.warnings,
        "risk_status": (row.features_json or {}).get("risk_status", "UNKNOWN"),
        "outcome": {"entry_vwap": (row.outcome_json or {}).get("entry_vwap_exact") or (str(row.entry_vwap) if row.entry_vwap is not None else None),
                     "price_basis": (row.outcome_json or {}).get("price_basis"),
                     **{f"reached_{n}x": None for n in (2, 3, 5, 10)},
                     "current_return": str(row.current_return) if row.current_return is not None else None,
                     "max_tick_return": str(row.max_tick_return) if row.max_tick_return is not None else None,
                     "max_realisable_return": str(row.max_realisable_return) if row.max_realisable_return is not None else None,
                     **_outcome_summary(row.outcome_json)},
    }


def _fresh_alpha_latest_rows(sync, *, now: datetime, fresh_seconds: int, limit: int) -> list[AlphaSnapshot]:
    """Rank one latest snapshot per fresh token/pair; freshness precedes LIMIT."""
    cutoff = now - timedelta(seconds=fresh_seconds)
    fresh = (
        select(
            AlphaSnapshot.token_address.label("token_address"),
            AlphaSnapshot.pair_address.label("pair_address"),
            func.max(AlphaSnapshot.snapshot_at).label("snapshot_at"),
        )
        .where(AlphaSnapshot.chain == "bsc", AlphaSnapshot.score_version == SCORE_VERSION,
               AlphaSnapshot.alpha_score.is_not(None), AlphaSnapshot.task_status.in_(("TRACKING", "COMPLETE")),
               AlphaSnapshot.snapshot_at <= now, AlphaSnapshot.pair_created_at >= cutoff)
        .group_by(AlphaSnapshot.token_address, AlphaSnapshot.pair_address)
        .subquery()
    )
    return sync.scalars(
        select(AlphaSnapshot)
        .where(AlphaSnapshot.chain == "bsc", AlphaSnapshot.score_version == SCORE_VERSION,
               AlphaSnapshot.alpha_score.is_not(None), AlphaSnapshot.task_status.in_(("TRACKING", "COMPLETE")))
        .join(
            fresh,
            and_(
                AlphaSnapshot.token_address == fresh.c.token_address,
                AlphaSnapshot.pair_address == fresh.c.pair_address,
                AlphaSnapshot.snapshot_at == fresh.c.snapshot_at,
            ),
        )
        .order_by(AlphaSnapshot.alpha_score.desc(), AlphaSnapshot.snapshot_at.desc())
        .limit(limit)
    ).all()


@alpha_router.get("/candidates")
async def alpha_candidates(limit: int = Query(100, ge=1, le=500)):
    settings = get_settings()
    now = datetime.now(UTC)
    async with session_scope(settings) as session:
        rows = await session.run_sync(
            lambda sync: _fresh_alpha_latest_rows(
                sync, now=now, fresh_seconds=settings.alpha_fresh_seconds, limit=limit
            )
        )
        metadata = await session.run_sync(lambda sync: load_alpha_metadata(sync, rows))
    return {"mode": "EXPERIMENTAL ALPHA", "shadow_mode": True, "candidates": [
        _alpha_payload(row, now=now, metadata=metadata.get((row.token_address, row.pair_address))) for row in rows
    ]}


@alpha_router.get("/candidates/{token_or_pair}")
async def alpha_candidate(token_or_pair: str):
    settings = get_settings()
    now = datetime.now(UTC)
    async with session_scope(settings) as session:
        rows = await session.run_sync(lambda sync: sync.scalars(
            select(AlphaSnapshot)
            .where(AlphaSnapshot.chain == "bsc", AlphaSnapshot.score_version == SCORE_VERSION)
            .where(AlphaSnapshot.task_status.in_(("PENDING_INPUT", "TRACKING", "COMPLETE")))
            .where((AlphaSnapshot.token_address == token_or_pair.lower()) | (AlphaSnapshot.pair_address == token_or_pair.lower()))
            .order_by(AlphaSnapshot.observation_seconds)
        ).all())
        metadata = await session.run_sync(lambda sync: load_alpha_metadata(sync, rows))
    if not rows:
        raise HTTPException(404, "alpha candidate not found")
    scored = [row for row in rows if row.alpha_score is not None]
    latest = max(scored or rows, key=lambda row: row.snapshot_at)
    return {
        "latest": _alpha_payload(latest, now=now, metadata=metadata.get((latest.token_address, latest.pair_address))),
        "snapshots": [_alpha_payload(row, now=now, metadata=metadata.get((row.token_address, row.pair_address))) for row in rows],
    }


@alpha_router.get("/stats")
async def alpha_stats():
    settings = get_settings()
    async with session_scope(settings) as session:
        return await session.run_sync(lambda sync: alpha_statistics(
            sync, now=datetime.now(UTC), minimum_quote=Decimal(str(settings.alpha_min_future_quote_notional)),
        ))


@alpha_router.get("/candidates/{token_or_pair}/revisions")
async def alpha_revisions(token_or_pair: str, limit: int = Query(50, ge=1, le=200),
                          before_id: int | None = Query(None, ge=1)):
    settings = get_settings()
    now = datetime.now(UTC)
    async with session_scope(settings) as session:
        rows, cursor = await session.run_sync(lambda sync: alpha_revision_history(
            sync, token_or_pair, limit=limit, before_id=before_id,
        ))
    return {"audit_only": True, "revisions": [_alpha_payload(row, now=now) for row in rows],
            "next_cursor": cursor}


def alpha_revision_history(sync, identity, *, limit=50, before_id=None):
    if not 1 <= limit <= 200 or (before_id is not None and before_id < 1):
        raise ValueError("invalid revision history page")
    query = select(AlphaSnapshot).where(
        AlphaSnapshot.chain == "bsc",
        (AlphaSnapshot.token_address == identity.lower()) | (AlphaSnapshot.pair_address == identity.lower()),
    )
    if before_id is not None:
        query = query.where(AlphaSnapshot.id < before_id)
    rows = sync.scalars(query.order_by(AlphaSnapshot.id.desc()).limit(limit + 1)).all()
    return rows[:limit], rows[limit - 1].id if len(rows) > limit else None


@alpha_router.get("/revisions/{snapshot_id}/lineage")
async def alpha_lineage(snapshot_id: int):
    async with session_scope(get_settings()) as session:
        row = await session.get(AlphaSnapshot, snapshot_id)
    if row is None or row.chain != "bsc":
        raise HTTPException(404, "alpha revision not found")
    return {"audit_only": True, "snapshot_id": row.id, "input_revision": row.input_revision,
            "supersedes_id": row.supersedes_id, "task_status": row.task_status,
            "input_manifest": row.input_manifest,
            "evaluation_manifest": (row.outcome_json or {}).get("evaluation_manifest"),
            "invalidated_outcome": (row.outcome_json or {}).get("invalidated_outcome")}


def alpha_statistics(sync, *, now, minimum_quote):
    """Aggregate a fixed 1m pair cohort; partial positives never enter rates."""
    current = (AlphaSnapshot.chain == "bsc", AlphaSnapshot.score_version == SCORE_VERSION)
    scored = (*current, AlphaSnapshot.alpha_score.is_not(None),
              AlphaSnapshot.task_status.in_(("TRACKING", "COMPLETE")))
    def count(*conditions, tokens=False):
        expression = func.count(func.distinct(AlphaSnapshot.token_address)) if tokens else func.count()
        return sync.scalar(select(expression).select_from(AlphaSnapshot).where(*conditions)) or 0
    cohort = (*scored, AlphaSnapshot.observation_seconds == COHORT_SECONDS,
              AlphaSnapshot.task_status == "COMPLETE", AlphaSnapshot.snapshot_at <= now - timedelta(hours=1),
              AlphaSnapshot.outcome_json["policy_id"].as_string() == policy_id(minimum_quote),
              AlphaSnapshot.outcome_json["tracking_complete"].as_boolean().is_(True),
              AlphaSnapshot.outcome_json["entry_coverage"]["status"].as_string() == "VERIFIED",
              AlphaSnapshot.outcome_json["future_coverage"]["status"].as_string() == "VERIFIED")
    def rates(high=False):
        conditions = (*cohort, AlphaSnapshot.alpha_score >= HIGH_SCORE) if high else cohort
        denominator = count(*conditions)
        return {str(n): {"hits": (hits := count(*conditions, AlphaSnapshot.outcome_json[f"reached_{n}x"].as_boolean().is_(True))),
                         "denominator": denominator, "rate": hits / denominator if denominator else None}
                for n in (2, 3, 5, 10)}
    return {"score_version": SCORE_VERSION, "label_version": LABEL_VERSION,
            "policy_id": policy_id(minimum_quote), "cohort_seconds": COHORT_SECONDS,
            "coverage_requirement": "VERIFIED_ENTRY_AND_FULL_1H",
            "scored_tokens": count(*scored, tokens=True),
            "high_score_tokens": count(*scored, AlphaSnapshot.alpha_score >= HIGH_SCORE, tokens=True),
            "pending_snapshots": count(*current, AlphaSnapshot.task_status.in_(("PENDING_INPUT", "TRACKING"))),
            "rebuilding_snapshots": count(*current, AlphaSnapshot.task_status == "PENDING_INPUT",
                                           AlphaSnapshot.supersedes_id.is_not(None)),
            "invalidated_snapshots": count(*current, AlphaSnapshot.task_status == "INVALIDATED"),
            "matured_1h": count(*cohort), "overall": rates(), "high_score": rates(True)}


@router.get("")
async def list_candidates(
    chain: str | None = None,
    status: CandidateStatus | None = None,
    min_priority: float = Query(0, ge=0, le=100),
    launchpad: str | None = None,
    risk: Literal["UNKNOWN", "LOW", "MEDIUM", "HIGH", "CRITICAL"] | None = None,
    age: int | None = Query(None, ge=0, description="Maximum age in seconds"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    sort: Literal["priority", "age", "liquidity", "volume"] = "priority",
    priority: float | None = Query(None, ge=0, le=100),
    smart_money: float | None = Query(None, ge=0, le=100),
    liquidity: float | None = Query(None, ge=0),
    volume: float | None = Query(None, ge=0),
):
    try:
        chain = validate_bsc_chain(chain)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    settings = get_settings()
    async with session_scope(settings) as session:
        result = await session.run_sync(
            lambda sync: candidates(
                sync,
                settings.radar,
                settings.bsc_chain_id,
                at=datetime.now(UTC),
                chain=chain,
                status=status,
                min_priority=min_priority,
                launchpad=launchpad,
                risk=risk,
                age=age,
                limit=limit,
                offset=offset, sort=sort, priority=priority, smart_money=smart_money,
                liquidity=liquidity, volume=volume,
            )
        )
        await session.commit()
        return result


@router.get("/{chain}/{token}")
async def get_candidate(chain: str, token: str):
    try:
        chain = validate_bsc_chain(chain)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    settings = get_settings()
    async with session_scope(settings) as session:
        result = await session.run_sync(
            lambda sync: candidate_detail(
                sync, settings.radar, settings.bsc_chain_id, chain, token, at=datetime.now(UTC)
            )
        )
        await session.commit()
    if result is None:
        raise HTTPException(404, "radar candidate not found")
    return result

@meta_router.get("/stats")
async def radar_stats():
    settings = get_settings()
    async with session_scope(settings) as session:
        def read(sync):
            rows = sync.execute(
                select(RadarCandidate._status, func.count()).group_by(RadarCandidate._status)
            ).all()
            return {"counts": {str(status.value if hasattr(status, 'value') else status): count for status, count in rows}, "total": sum(count for _, count in rows)}
        result = await session.run_sync(read)
    result.update({"as_of": datetime.now(UTC), "observed_at": datetime.now(UTC), "updated_at": datetime.now(UTC), "freshness": "FRESH", "data_freshness_seconds": 0, "source_status": "OK"})
    return result


@meta_router.get("/live-performance")
async def live_performance():
    """Forward observations only; historical backtest is intentionally separate."""
    settings = get_settings()
    async with session_scope(settings) as session:
        result = await session.run_sync(lambda sync: performance(sync, at=datetime.now(UTC)))
    return result

@meta_router.get("/health")
async def radar_health():
    settings = get_settings()
    redis = await check_redis(settings)
    if redis == "ok":
        source_health_registry.record_success("redis")
    else:
        source_health_registry.record_failure("redis", "redis unavailable")
    # Radar health is served by the API process, while BSC transport runs in
    # a separate worker. Project the shared durable state here as well so the
    # UI cannot overwrite a current worker status with this process's empty
    # in-memory registry.
    try:
        async with session_scope(settings) as session:
            def read(sync):
                live_tail = sync.scalar(
                    select(RuntimeWorkerStatus).where(
                        RuntimeWorkerStatus.worker_name == "bsc-live-tail"
                    )
                )
                backfill = sync.scalar(
                    select(RuntimeWorkerStatus).where(
                        RuntimeWorkerStatus.worker_name == "bsc-ingestion"
                    )
                )
                return (
                    live_tail or backfill,
                    sync.scalar(
                        select(StreamCheckpoint).where(
                            StreamCheckpoint.chain_id == BSC_CHAIN_ID,
                            StreamCheckpoint.stream_name == "bsc_realtime",
                        )
                    ),
                )
            runtime_status, stream_checkpoint = await session.run_sync(read)
        from smms.api.app import _persisted_bsc_health

        for source, state in _persisted_bsc_health(
            runtime_status,
            stream_checkpoint,
            ws_configured=bool(settings.bsc_ws_endpoints),
            http_configured=bool(settings.bsc_rpc_endpoints),
            now=datetime.now(UTC),
        ).items():
            source_health_registry.set_status(source, state["status"], details=state["details"])
    except Exception as exc:  # noqa: BLE001 - health endpoint must remain best-effort
        logger.debug("persisted BSC health unavailable: %s", type(exc).__name__)
    metrics.record_sse_client_count(event_bus.subscriber_count)
    snapshot = observability.snapshot()
    observed_at = event_bus.last_published_at
    freshness = freshness_status(observed_at) if observed_at else "UNKNOWN"
    return {
        "status": "ok" if redis == "ok" else "degraded",
        "redis": redis,
        "database_canonical": True,
        "event_buffer": event_bus.max_events,
        "connected_clients": event_bus.subscriber_count,
        "source_status": "OK" if redis == "ok" else "DEGRADED",
        "as_of": datetime.now(UTC),
        "observed_at": event_bus.last_published_at,
        "updated_at": datetime.now(UTC),
        "freshness": freshness if redis == "ok" else "STALE",
        "sources": snapshot["sources"],
        "source_health": snapshot["sources"],
        "metrics": snapshot["metrics"],
    }

@meta_router.get("/events")
async def radar_events(request: Request):
    last_id = request.headers.get("last-event-id") or request.query_params.get("last_event_id")
    event_types = set(filter(None, request.query_params.get("event_types", "").split(","))) or None
    try:
        chain = validate_bsc_chain(request.query_params.get("chain"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    token = request.query_params.get("token")
    async def stream():
        queue = await event_bus.subscribe(last_id, event_types=event_types, chain=chain, token=token, require_sync=True)
        try:
            # Flush the connection immediately even when the market is quiet.
            yield ": connected\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                    yield sse_encode(event)
                except TimeoutError:
                    yield ": heartbeat\n\n"
        finally:
            event_bus.unsubscribe(queue)
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"})

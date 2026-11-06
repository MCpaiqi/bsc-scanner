"""Public aggregate views; no raw payloads or storage identifiers."""

from datetime import UTC, datetime

from sqlalchemy import select

from smms.db.facts import canonical_fact_conditions
from smms.db.models import Pair, RadarCandidate, Signal, Token
from smms.evidence import EvidenceResolver
from smms.market.identity import display_text
from smms.observability import freshness_status

from .domain import CandidateStatus
from .engine import UNKNOWN, normalized, utc


def candidate_view(session, candidate):
    token = session.get(Token, candidate.token_id)
    signal = session.get(Signal, candidate.latest_signal_id) if candidate.latest_signal_id else None
    from smms.external_evidence.chain import canonical_observations
    from smms.external_evidence.orchestrator import ExternalEvidenceOrchestrator
    from smms.research.state import capture_epoch
    from smms.research.visibility import is_current_snapshot

    from .engine import RadarEngine
    from .features import RadarFeatureSnapshot
    from .projection import projection_for

    projection = projection_for(session, candidate)
    legacy = projection is None and candidate.projection_id is None and capture_epoch(session) == 0
    pending = projection is None and not legacy
    usable = legacy or (projection is not None and projection.snapshot_status == 'VALID')
    if projection is not None and usable:
        signal = session.scalar(select(Signal).join(RadarCandidate, RadarCandidate.latest_signal_id == Signal.id).where(
            RadarCandidate.id == candidate.id, Signal.created_at <= projection.as_of,
        ).execution_options(populate_existing=True))
    if signal is not None and (not usable or not is_current_snapshot(session, signal)):
        signal = None
    features = (projection.features_json if projection is not None else candidate.features_json if legacy
                else RadarFeatureSnapshot.unknown(age_seconds=0, market_phase=UNKNOWN).as_dict())
    ranking = projection.priority_json if projection is not None else candidate.priority_json if legacy else {}
    data_status = 'REBUILDING' if pending else projection.data_status if projection is not None else candidate.data_status
    observations = ExternalEvidenceOrchestrator(session, providers=[]).load(token.chain, token.address, as_of=datetime.now(UTC))
    observations.extend(canonical_observations(RadarEngine(session), candidate, as_of=datetime.now(UTC)))
    evidence_payload = EvidenceResolver().summary(observations)
    now = utc(datetime.now(UTC))
    observed_at = utc(candidate.latest_seen_at) if candidate.latest_seen_at else None
    updated_at = utc(candidate.updated_at) if candidate.updated_at else None
    freshness_seconds = (
        max(0, int((now - observed_at).total_seconds())) if observed_at else None
    )
    freshness = freshness_status(
        observed_at,
        now=now,
        fresh_after_seconds=60,
        stale_after_seconds=3600,
    )
    if pending:
        freshness, observed_at, freshness_seconds = "UNKNOWN", None, None
    source_status = (
        "REBUILDING" if pending else "OK"
        if data_status not in {"UNKNOWN", "ORPHANED", "REBUILDING"} and freshness == "FRESH"
        else "STALE"
        if freshness in {"STALE", "EXPIRED"}
        else data_status
    )
    pair = session.scalar(
        select(Pair).where(
            Pair.chain_id == 56,
            *canonical_fact_conditions(Pair), Pair.creation_timestamp <= now,
            (Pair.token0_address == token.address) | (Pair.token1_address == token.address),
        ).order_by(Pair.creation_block.desc().nullslast(), Pair.id.desc()).limit(1).execution_options(populate_existing=True)
    )
    platform = "PancakeSwap V2" if pair is not None and pair.dex == "pancakeswap" else UNKNOWN
    return {
        "chain": token.chain,
        "token_address": token.address,
        "pair_address": pair.address if pair is not None else None,
        "symbol": display_text(token.symbol, token.address),
        "name": display_text(token.name, token.address),
        "launchpad": ((projection.context_json.get("launchpad") if projection else None)
                      or (token.launchpad if legacy else None) or platform),
        "discovered_at": candidate.discovered_at,
        "discovery_source": candidate.discovery_source,
        "discovery_reason": candidate.discovery_reason,
        "first_seen_block": candidate.first_seen_block,
        "latest_seen_at": candidate.latest_seen_at,
        "status": candidate.status.value if usable else "REJECTED" if projection is not None else UNKNOWN,
        "stage": projection.context_json.get("stage", UNKNOWN) if projection is not None else candidate.stage if legacy else UNKNOWN,
        "priority_score": float(ranking.get("priority_score", candidate.priority_score if legacy else 0)),
        "components": ranking.get("components", {}),
        "priority_policy": ranking.get("policy", "radar-priority-v1"),
        "priority_config": ranking.get("config", {}),
        "features": features,
        "data_status": data_status,
        "research": {"source_epoch": projection.source_epoch, "revision": projection.research_revision,
                     "digest": projection.input_manifest.get("digest"),
                     "lineage_url": f"/api/research/snapshots/radar_feature_observations/{projection.id}"} if projection else None,
        "evidence": evidence_payload,
        "latest_signal_class": signal.signal_type.value if signal else UNKNOWN,
        "latest_signal_score": float(signal.signal_strength)
        if signal and signal.signal_strength is not None
        else UNKNOWN,
        "latest_signal_research": {"source_epoch": signal.source_epoch, "revision": signal.research_revision,
                                   "digest": signal.input_manifest.get("digest"),
                                   "lineage_url": f"/api/research/snapshots/signals/{signal.id}"} if signal else None,
        "as_of": updated_at,
        "observed_at": observed_at,
        "updated_at": updated_at,
        "freshness": freshness,
        "data_freshness_seconds": freshness_seconds,
        "source_status": source_status,
    }


def candidates(
    session,
    config,
    chain_id,
    *,
    at: datetime,
    chain=None,
    status=None,
    min_priority=0,
    launchpad=None,
    risk=None,
    age=None,
    limit=100,
    offset=0,
    sort="priority",
    priority=None,
    smart_money=None,
    liquidity=None,
    volume=None,
):
    query = select(RadarCandidate).join(Token, Token.id == RadarCandidate.token_id)
    if chain:
        query = query.where(Token.chain == chain)
    views = []
    # GETs read the durable projection. Ingestion and the radar worker own refreshes.
    for row in session.scalars(query.order_by(RadarCandidate.id)).all():
        view = candidate_view(session, row)
        if launchpad and view["launchpad"] != launchpad:
            continue
        if status is not None and view["status"] != status.value:
            continue
        if status is None and view["status"] in {CandidateStatus.REJECTED.value, CandidateStatus.STALE.value}:
            continue
        if view["priority_score"] < min_priority:
            continue
        if priority is not None and view["priority_score"] < priority:
            continue
        features = view["features"] or {}
        if smart_money is not None:
            score = features.get("smart_money_score", UNKNOWN)
            try:
                if float(score) < smart_money:
                    continue
            except (TypeError, ValueError):
                continue
        if liquidity is not None:
            try:
                if float(features.get("liquidity", 0)) < liquidity:
                    continue
            except (TypeError, ValueError):
                continue
        if volume is not None:
            try:
                if float(features.get("volume_5m", features.get("volume", 0))) < volume:
                    continue
            except (TypeError, ValueError):
                continue
        if risk and features.get("risk", UNKNOWN) != risk:
            continue
        if age is not None and (utc(at) - utc(row.discovered_at)).total_seconds() > age:
            continue
        views.append(view)
    if sort == "age":
        views.sort(key=lambda v: (v.get("latest_seen_at") or v.get("discovered_at") or at, v["token_address"]))
    elif sort == "liquidity":
        views.sort(key=lambda v: -(float((v.get("features") or {}).get("liquidity", 0)) if str((v.get("features") or {}).get("liquidity", 0)).replace(".", "", 1).isdigit() else 0))
    elif sort == "volume":
        views.sort(key=lambda v: -(float((v.get("features") or {}).get("volume_5m", 0)) if str((v.get("features") or {}).get("volume_5m", 0)).replace(".", "", 1).isdigit() else 0))
    else:
        views.sort(key=lambda v: (-v["priority_score"], v["chain"], v["token_address"]))
    page = views[offset:offset + limit]
    ages = [item["data_freshness_seconds"] for item in page if item.get("data_freshness_seconds") is not None]
    freshness_values = {str(item.get("freshness", "UNKNOWN")) for item in page}
    aggregate_freshness = (
        "EXPIRED" if "EXPIRED" in freshness_values else
        "STALE" if "STALE" in freshness_values else
        "FRESH" if page and freshness_values == {"FRESH"} else
        "UNKNOWN"
    )
    return {
        "candidates": page,
        "count": len(page),
        "total": len(views),
        "offset": offset,
        "limit": limit,
        "as_of": at,
        "observed_at": at,
        "updated_at": at,
        "freshness": aggregate_freshness,
        "data_freshness_seconds": max(ages) if ages else None,
        "source_status": "STALE" if aggregate_freshness in {"STALE", "EXPIRED"} else "OK",
    }


def candidate_detail(session, config, chain_id, chain, token, *, at):
    address = normalized(chain, token)
    from sqlalchemy import func

    query = (
        select(RadarCandidate)
        .join(Token, Token.id == RadarCandidate.token_id)
        .where(Token.chain == chain)
    )
    query = (
        query.where(func.lower(Token.address) == address)
        if chain == "bsc"
        else query.where(Token.address == address)
    )
    row = session.scalar(query)
    if row is None:
        return None
    return candidate_view(session, row)

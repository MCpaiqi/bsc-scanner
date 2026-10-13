"""Public aggregate views; no raw payloads or storage identifiers."""

from datetime import UTC, datetime

from sqlalchemy import select

from smms.db.models import Pair, RadarCandidate, RadarCandidateEvidence, Signal, Token
from smms.evidence import EvidenceResolver
from smms.external_evidence.models import EvidenceObservation
from smms.market.identity import display_text
from smms.observability import freshness_status

from .domain import CandidateStatus
from .engine import UNKNOWN, normalized, utc


def candidate_view(session, candidate):
    token = session.get(Token, candidate.token_id)
    signal = session.get(Signal, candidate.latest_signal_id) if candidate.latest_signal_id else None
    evidence_payload = (token.metadata_json or {}).get("evidence", {}) if isinstance(token.metadata_json, dict) else {}
    if isinstance(evidence_payload, list):
        try:
            def observation(item):
                item = dict(item)
                for key in ("observed_at", "fetched_at", "expires_at"):
                    value = item.get(key)
                    if isinstance(value, str):
                        item[key] = datetime.fromisoformat(value)
                return EvidenceObservation(**item)
            evidence_payload = EvidenceResolver().summary(
                [observation(item) for item in evidence_payload]
            )
        except (TypeError, ValueError):
            evidence_payload = {}
    canonical = session.scalars(
        select(RadarCandidateEvidence).where(
            RadarCandidateEvidence.candidate_id == candidate.id,
            RadarCandidateEvidence.valid.is_(True),
        )
    ).all()
    for item in canonical:
        evidence_payload.setdefault(item.source_key, {
            "value": True,
            "resolved_value": True,
            "source": "CHAIN",
            "resolution_source": item.source_kind,
            "evidence_level": "ON_CHAIN_VERIFIED",
            "confidence": 1.0,
            "freshness": "FRESH",
            "conflict": False,
            "conflicting_sources": [],
        })
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
    source_status = (
        "OK"
        if candidate.data_status not in {"UNKNOWN", "ORPHANED"} and freshness == "FRESH"
        else "STALE"
        if freshness in {"STALE", "EXPIRED"}
        else candidate.data_status
    )
    pair = session.scalar(
        select(Pair).where(
            Pair.chain_id == 56,
            Pair.canonical.is_(True),
            Pair.removed.is_(False),
            (Pair.token0_address == token.address) | (Pair.token1_address == token.address),
        ).order_by(Pair.creation_block.desc().nullslast(), Pair.id.desc())
    )
    platform = "PancakeSwap V2" if pair is not None and pair.dex == "pancakeswap" else UNKNOWN
    return {
        "chain": token.chain,
        "token_address": token.address,
        "pair_address": pair.address if pair is not None else None,
        "symbol": display_text(token.symbol, token.address),
        "name": display_text(token.name, token.address),
        "launchpad": token.launchpad or platform,
        "discovered_at": candidate.discovered_at,
        "discovery_source": candidate.discovery_source,
        "discovery_reason": candidate.discovery_reason,
        "first_seen_block": candidate.first_seen_block,
        "latest_seen_at": candidate.latest_seen_at,
        "status": candidate.status.value,
        "stage": candidate.stage,
        "priority_score": float(candidate.priority_score),
        "components": candidate.priority_json.get("components", {}),
        "priority_policy": candidate.priority_json.get("policy", "radar-priority-v1"),
        "priority_config": candidate.priority_json.get("config", {}),
        "features": candidate.features_json,
        "data_status": candidate.data_status,
        "evidence": evidence_payload,
        "latest_signal_class": signal.signal_type.value if signal else UNKNOWN,
        "latest_signal_score": float(signal.signal_strength)
        if signal and signal.signal_strength is not None
        else UNKNOWN,
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
    if launchpad:
        query = query.where(Token.launchpad == launchpad)
    views = []
    # GETs read the durable projection. Ingestion and the radar worker own refreshes.
    for row in session.scalars(query.order_by(RadarCandidate.id)).all():
        if status is not None and row.status != status:
            continue
        if status is None and row.status in {CandidateStatus.REJECTED, CandidateStatus.STALE}:
            continue
        if row.priority_score < min_priority:
            continue
        if priority is not None and row.priority_score < priority:
            continue
        features = row.features_json or {}
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
        if risk and row.features_json.get("risk", UNKNOWN) != risk:
            continue
        if age is not None and (utc(at) - utc(row.discovered_at)).total_seconds() > age:
            continue
        views.append(candidate_view(session, row))
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

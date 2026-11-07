"""Public aggregate views; no raw payloads or storage identifiers."""

from datetime import UTC, datetime

from sqlalchemy import select

from smms.db.facts import canonical_fact_conditions
from smms.db.models import Pair, RadarCandidate, Signal, Token
from smms.evidence import EvidenceResolver
from smms.market.identity import display_text
from smms.observability import freshness_status
from smms.research.lineage import json_value
from smms.research.state import capture_epoch

from .engine import UNKNOWN, normalized, utc


def candidate_view(session, candidate, *, at=None, selection=None):
    at = utc(at or datetime.now(UTC))
    token = selection.token if selection else session.get(Token, candidate.token_id, populate_existing=True)
    signal = selection.signal if selection else (session.get(Signal, candidate.latest_signal_id, populate_existing=True)
                                                if candidate.latest_signal_id else None)
    from smms.external_evidence.chain import canonical_observations
    from smms.external_evidence.orchestrator import ExternalEvidenceOrchestrator
    from smms.research.visibility import is_current_snapshot

    from .engine import RadarEngine
    from .features import RadarFeatureSnapshot
    from .projection import projection_for

    projection = selection.projection if selection else projection_for(session, candidate)
    if projection is not None and (projection.candidate_id != candidate.id or projection.chain != token.chain
                                   or utc(projection.as_of) > at):
        projection = None
    legacy = projection is None and candidate.projection_id is None and capture_epoch(session) == 0
    pending = projection is None and not legacy
    usable = legacy or (projection is not None and projection.snapshot_status == 'VALID')
    if projection is not None and usable and selection is None:
        signal = session.scalar(select(Signal).join(RadarCandidate, RadarCandidate.latest_signal_id == Signal.id).where(
            RadarCandidate.id == candidate.id, Signal.created_at <= projection.as_of,
        ).execution_options(populate_existing=True))
    if signal is not None and (not usable or signal.token_id != token.id or signal.chain != token.chain
                               or utc(signal.created_at) > at or (selection is None and not is_current_snapshot(session, signal))):
        signal = None
    features = (projection.features_json if projection is not None else candidate.features_json if legacy
                else RadarFeatureSnapshot.unknown(age_seconds=0, market_phase=UNKNOWN).as_dict())
    ranking = projection.priority_json if projection is not None else candidate.priority_json if legacy else {}
    data_status = 'REBUILDING' if pending else projection.data_status if projection is not None else candidate.data_status
    observations = ExternalEvidenceOrchestrator(session, providers=[]).load(token.chain, token.address, as_of=at)
    observations.extend(canonical_observations(RadarEngine(session), candidate, as_of=at))
    evidence_payload = EvidenceResolver().summary(observations)
    now = at
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
    pair = selection.pair if selection else session.scalar(
        select(Pair).where(
            Pair.chain_id == 56,
            *canonical_fact_conditions(Pair), Pair.creation_timestamp <= now,
            (Pair.token0_address == token.address) | (Pair.token1_address == token.address),
        ).order_by(Pair.creation_block.desc().nullslast(), Pair.id.desc()).limit(1).execution_options(populate_existing=True)
    )
    platform = "PancakeSwap V2" if pair is not None and pair.dex == "pancakeswap" else UNKNOWN
    result = {
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
        "priority_score": (float(selection.values["priority"]) if selection.values["priority"] is not None else UNKNOWN)
        if selection else float(ranking.get("priority_score", candidate.priority_score if legacy else 0)),
        "components": ranking.get("components", {}),
        "priority_policy": ranking.get("policy", "radar-priority-v1"),
        "priority_config": ranking.get("config", {}),
        "features": features,
        "data_status": data_status,
        "research": {"source_epoch": projection.source_epoch, "revision": projection.research_revision,
                     "digest": selection.projection_digest if selection else projection.input_manifest.get("digest"),
                     "lineage_url": f"/api/research/snapshots/radar_feature_observations/{projection.id}"} if projection else None,
        "evidence": evidence_payload,
        "latest_signal_class": signal.signal_type.value if signal else UNKNOWN,
        "latest_signal_score": float(signal.signal_strength)
        if signal and signal.signal_strength is not None
        else UNKNOWN,
        "latest_signal_research": {"source_epoch": signal.source_epoch, "revision": signal.research_revision,
                                   "digest": selection.signal_digest if selection else signal.input_manifest.get("digest"),
                                   "lineage_url": f"/api/research/snapshots/signals/{signal.id}"} if signal else None,
        "as_of": updated_at,
        "observed_at": observed_at,
        "updated_at": updated_at,
        "freshness": freshness,
        "data_freshness_seconds": freshness_seconds,
        "source_status": source_status,
    }

    if selection is not None:
        values = selection.values
        result.update({"priority_score": float(values["priority"]) if values["priority"] is not None else UNKNOWN, "status": values["status"],
                       "launchpad": values["launchpad"], "freshness": values["freshness"],
                       "source_status": values["source_status"],
                       "latest_signal_score": float(values["signal_score"]) if values["signal_score"] is not None else UNKNOWN})
        result["features"] = {**features, "risk": values["risk"],
                              **{field: json_value(values[key]) if values[key] is not None else UNKNOWN
                                 for field, key in (("liquidity", "liquidity"), ("volume_5m", "volume"),
                                                    ("smart_money_score", "smart_money"))}}
    return result


def candidates(session, config, chain_id, *, at, chain=None, status=None, min_priority=None,
               launchpad=None, risk=None, age=None, limit=100, offset=0, sort="priority", priority=None,
               smart_money=None, liquidity=None, volume=None, q=None, direction="desc", view="all",
               freshness="all", include_stale=False):
    from .query import finish_read, select_candidates

    at, chain = utc(at), chain or "bsc"
    epoch = capture_epoch(session, chain)
    selected, metadata = select_candidates(session, at=at, epoch=epoch, chain=chain, status=status,
        min_priority=min_priority, launchpad=launchpad, risk=risk, age=age, limit=limit, offset=offset,
        sort=sort, priority=priority, smart_money=smart_money, liquidity=liquidity, volume=volume,
        q=q, direction=direction, view=view, freshness=freshness, include_stale=include_stale)
    page = [candidate_view(session, row.candidate, at=at, selection=row) for row in selected]
    finish_read(session, epoch, selected, chain)
    ages = [item["data_freshness_seconds"] for item in page if item.get("data_freshness_seconds") is not None]
    fresh_values = {item.get("freshness", "UNKNOWN") for item in page}
    aggregate = ("UNKNOWN" if not page or "UNKNOWN" in fresh_values else "EXPIRED" if "EXPIRED" in fresh_values
                 else "STALE" if "STALE" in fresh_values else "FRESH")
    if not page:
        source = "OUT_OF_RANGE" if metadata["total"] else "NO_MATCHES" if metadata["universe_total"] else "EMPTY"
    elif all(row["source_status"] == "REBUILDING" for row in page):
        source = "REBUILDING"
    elif aggregate in {"STALE", "EXPIRED"}:
        source = "STALE"
    elif any(row["source_status"] != "OK" for row in page):
        source = "UNKNOWN" if all(row["source_status"] != "OK" for row in page) else "PARTIAL"
    else:
        source = "OK"
    observed = [row["observed_at"] for row in page if row.get("observed_at")]
    updated = [row["updated_at"] for row in page if row.get("updated_at")]
    return {**metadata, "candidates": page, "count": len(page), "offset": offset, "limit": limit,
            "has_more": offset + len(page) < metadata["total"],
            "next_offset": offset + limit if offset + len(page) < metadata["total"] else None,
            "as_of": at, "observed_at": max(observed) if observed else None,
            "updated_at": max(updated) if updated else None, "freshness": aggregate,
            "data_freshness_seconds": max(ages) if ages else None, "source_status": source}


def candidate_detail(session, config, chain_id, chain, token, *, at):
    from .query import finish_read, select_candidates

    at = utc(at)
    epoch = capture_epoch(session, chain)
    selected, _ = select_candidates(session, at=at, epoch=epoch, chain=chain,
                                     exact_token=normalized(chain, token), include_stale=True,
                                     include_rejected=True, limit=1)
    if not selected:
        return None
    result = candidate_view(session, selected[0].candidate, at=at, selection=selected[0])
    finish_read(session, epoch, selected, chain)
    return result

"""Canonical first-observation publication and exact-cutoff reconstruction."""

from sqlalchemy import select

from smms.db.models import (
    LiveSignalObservation,
    RadarCandidate,
    RadarFeatureObservation,
    Signal,
    Token,
)
from smms.evaluation import utc
from smms.research.dependencies import (
    DependenciesPending,
    current_revision,
    require_rebuilt_dependencies,
)
from smms.research.lineage import manifest
from smms.research.publication import lock_identity, publish_snapshot
from smms.research.state import capture_epoch
from smms.research.visibility import is_current_snapshot

from .config import RadarConfig
from .engine import RadarEngine
from .forward_prices import (
    ENTRY_MAX_AGE,
    load_usd_prices,
    price_dependencies,
    price_facts,
    select_entry_price,
)
from .projection import build_projection, projection_for, radar_config_for


def reference(row):
    return {"kind": row.__tablename__, "id": row.id, "digest": (row.input_manifest or {}).get("digest")}


def root_id(session, observation):
    return session.scalar(select(LiveSignalObservation.id).where(
        LiveSignalObservation.chain == observation.chain,
        LiveSignalObservation.candidate_id == observation.candidate_id,
        LiveSignalObservation.signal_class == observation.signal_class,
    ).order_by(LiveSignalObservation.research_revision, LiveSignalObservation.id).limit(1)
        .execution_options(include_invalid_snapshots=True))


def publish_observation(session, candidate, signal_class, *, at, reason, old=None):
    from .live_forward import _decimal, _sample_rejected, enqueue_horizons

    epoch, at = capture_epoch(session), utc(at)
    token = session.get(Token, candidate.token_id, populate_existing=True)
    if token is None or token.chain != "bsc":
        return None
    lock_identity(session, LiveSignalObservation, {"chain": token.chain, "candidate_id": candidate.id, "signal_class": signal_class})
    latest = session.scalar(select(LiveSignalObservation).where(
        LiveSignalObservation.chain == token.chain, LiveSignalObservation.candidate_id == candidate.id,
        LiveSignalObservation.signal_class == signal_class,
    ).order_by(LiveSignalObservation.research_revision.desc()).limit(1)
        .execution_options(include_invalid_snapshots=True, populate_existing=True))
    if old is None and latest is not None and is_current_snapshot(session, latest):
        return latest
    if latest is not None:
        old = latest
        at = utc(old.observed_at)
        reason = (old.input_manifest or {}).get("parameters", {}).get("reason", (old.t0_snapshot_json or {}).get("reason", reason))
    if old is not None:
        require_rebuilt_dependencies(session, old)
        parameters = (old.input_manifest or {}).get("parameters", {})
        saved = old.t0_snapshot_json or {}
        source_id = parameters.get("projection_id") or saved.get("projection_id")
        source = session.scalar(select(RadarFeatureObservation).where(RadarFeatureObservation.id == source_id)
                                .execution_options(include_invalid_snapshots=True)) if source_id else None
        config = (source.input_manifest or {}).get("parameters", {}).get("config", {}) if source else parameters.get("radar_config", {})
        projection = build_projection(RadarEngine(session, RadarConfig(**config)), candidate,
                                      at=source.as_of if source else at,
                                      config_hash=source.config_hash if source else None,
                                      reconstruction_policy=parameters.get("reconstruction_policy") or ("legacy-forward-upgrade-v1" if not parameters else None))
        signal_id = parameters.get("signal_id")
        signal_source = session.scalar(select(Signal).where(Signal.id == signal_id)
                                       .execution_options(include_invalid_snapshots=True)) if signal_id else None
        signal = current_revision(session, signal_source) if signal_source else None
        if signal_source is not None and signal is None:
            raise DependenciesPending("Forward signal prerequisite is rebuilding")
        policy = parameters.get("reconstruction_policy") or ("legacy-forward-upgrade-v1" if not parameters else None)
        sample_group = old.sample_group
    else:
        projection = projection_for(session, candidate)
        if projection is not None and utc(projection.as_of) > at and signal_class == "MARKET_BASELINE":
            projection = build_projection(RadarEngine(session, radar_config_for(session, candidate)), candidate, at=at)
        if projection is None or utc(projection.as_of) > at or projection.snapshot_status != "VALID":
            return None
        signal = session.scalar(select(Signal).where(Signal.id == candidate.latest_signal_id)
                                .execution_options(populate_existing=True)) if candidate.latest_signal_id else None
        if signal is not None and (not is_current_snapshot(session, signal) or utc(signal.created_at) > at):
            signal = None
        if signal_class == "MARKET_BASELINE" and not _sample_rejected(f"{token.chain}:{token.address}", at, rate=10):
            return None
        sample_group = "REJECTED_UNSAMPLED" if signal_class == "REJECTED" and not _sample_rejected(token.address, at) else signal_class
        policy = None
    features = projection.features_json
    lineage = {}
    prices = load_usd_prices(session, [(token.address, at - ENTRY_MAX_AGE, at)], as_of=at, lineage=lineage).get(token.address, [])
    entry = select_entry_price(prices, at=at)
    score = signal.signal_strength if signal is not None and signal.snapshot_status == "VALID" else None
    metrics = select_entry_price([p for p in prices if entry and p.provenance["pair_address"] == entry.provenance["pair_address"]
                                  and p.provenance["model"] == "TokenMarketSnapshot"], at=at)
    market_cap = _decimal(metrics.provenance.get("market_cap_usd")) if metrics else None
    liquidity = _decimal(metrics.provenance.get("liquidity_usd")) if metrics else None
    config = (projection.input_manifest or {}).get("parameters", {}).get("config", {})
    parameters = {"recipe": "canonical-forward-observation-v1", "candidate_id": candidate.id,
                  "observed_at": at, "signal_class": signal_class, "reason": reason,
                  "projection_id": projection.id, "signal_id": signal.id if signal else None,
                  "radar_config": config, "cohort_policy": "recorded-transition-cohort-v1",
                  "reconstruction_policy": policy}
    dependencies = [reference(projection), *([reference(signal)] if signal else []), *price_dependencies(lineage, prices)]
    snapshot = {"token": token.address, "observed_at": at.isoformat(), "signal_class": signal_class,
                "signal_score": str(score) if score is not None else None,
                "price": str(entry.price) if entry else None, "entry_price_exact": str(entry.price) if entry else None,
                "price_basis": "USD" if entry else "UNKNOWN", "entry_source": dict(entry.provenance) if entry else None,
                "entry_max_age_seconds": int(ENTRY_MAX_AGE.total_seconds()),
                "pair_address": entry.provenance["pair_address"] if entry else None, "reason": reason,
                "market_cap": str(market_cap) if market_cap is not None else None,
                "liquidity": str(liquidity) if liquidity is not None else None,
                "metric_source": dict(metrics.provenance) if metrics else None,
                "smart_money_snapshot_id": projection.smart_snapshot_id, "risk_snapshot_id": projection.risk_snapshot_id,
                "projection_id": projection.id, "projection_epoch": projection.source_epoch,
                "projection_digest": (projection.input_manifest or {}).get("digest"),
                "cohort_policy": parameters["cohort_policy"], "reconstruction_policy": policy,
                "observation_policy": "discovery-event-time-v1" if signal_class == "MARKET_BASELINE" else "observed-transition-v1",
                "feature_version": features.get("feature_version", "radar-features-v1"),
                "config_version": signal.config_version if signal else "UNKNOWN",
                "model_version": signal.model_version if signal else "UNKNOWN"}
    if capture_epoch(session) != epoch:
        raise ValueError("canonical epoch changed during Forward observation")
    unavailable = projection.snapshot_status != "VALID" or (signal is not None and signal.snapshot_status != "VALID")
    row = publish_snapshot(session, LiveSignalObservation(
        chain=token.chain, candidate_id=candidate.id, token_id=token.id, token_address=token.address,
        observed_at=at, signal_class=signal_class, signal_score=score, price=entry.price if entry and not unavailable else None,
        market_cap=market_cap, liquidity=liquidity, smart_money_snapshot_id=projection.smart_snapshot_id,
        risk_snapshot_id=projection.risk_snapshot_id, feature_version=snapshot["feature_version"],
        config_version=snapshot["config_version"], model_version=snapshot["model_version"], sample_group=sample_group,
        t0_snapshot_json=snapshot, source_epoch=epoch, snapshot_status="UNAVAILABLE" if unavailable else "VALID",
        input_manifest=manifest(kind="live_signal_observations", parameters=parameters,
                                facts=price_facts(lineage, prices), dependencies=dependencies,
                                notes=["CANONICAL_DISCOVERY_OR_SIGNAL_WITHDRAWN"] if unavailable else []),
    ))
    enqueue_horizons(session, row)
    return row


def rebuild_observation(session, old):
    parameters = (old.input_manifest or {}).get("parameters", {})
    if parameters.get("recipe", "canonical-forward-observation-v1") != "canonical-forward-observation-v1":
        return "UNSUPPORTED_VERSION"
    candidate = session.get(RadarCandidate, old.candidate_id)
    if candidate is None:
        return "CANDIDATE_DELETED"
    publish_observation(session, candidate, old.signal_class, at=old.observed_at,
                        reason=parameters.get("reason", (old.t0_snapshot_json or {}).get("reason", "LEGACY_RECONSTRUCTION")), old=old)
    return None

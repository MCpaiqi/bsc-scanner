"""Canonical immutable Radar projections and guarded mutable cache pointers."""

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import func, select

from smms.db.models import (
    Pair,
    RadarCandidate,
    RadarCandidateEvidence,
    RadarFeatureObservation,
    ResearchState,
    Signal,
    Token,
    TokenLifecycleEvent,
    TradeEvent,
)
from smms.evaluation import utc
from smms.research.lineage import digest, json_value, manifest, row_values
from smms.research.publication import publish_snapshot
from smms.research.state import capture_epoch

from .domain import CandidateStatus as S
from .features import UNKNOWN, RadarFeatureSnapshot
from .priority import priority


class StaleProjectionInputs(ValueError):
    pass


def radar_config_for(session, candidate, fallback=None):
    from .config import RadarConfig

    saved = session.scalar(select(RadarFeatureObservation.input_manifest).join(
        RadarCandidate, RadarCandidate.projection_id == RadarFeatureObservation.id,
    ).where(RadarCandidate.id == candidate.id).execution_options(include_invalid_snapshots=True))
    parameters = (saved or {}).get("parameters", {})
    return RadarConfig(**parameters["config"]) if parameters.get("config") else fallback or RadarConfig()


def canonical_proofs(engine, candidate, *, at):
    from .engine import SOURCES, observed

    token = engine.session.get(Token, candidate.token_id)
    valid = []
    proofs = engine.session.scalars(select(RadarCandidateEvidence).where(
        RadarCandidateEvidence.candidate_id == candidate.id,
    ).order_by(RadarCandidateEvidence.source_key).execution_options(populate_existing=True)).all()
    for proof in proofs:
        descriptor = SOURCES.get(proof.source_key.split(":")[0])
        if descriptor is None:
            continue
        model, column = descriptor
        source_id = getattr(proof, column)
        row = engine.session.get(model, source_id, populate_existing=True) if source_id else None
        if (engine.canonical(row, token.chain, proof) and engine.source_kind(row) == proof.source_kind
                and utc(observed(row)) <= at):
            valid.append((row, proof))
    return sorted(valid, key=lambda item: (utc(observed(item[0])), item[1].source_key))


def _context(valid):
    from .engine import observed

    first, proof = valid[0]
    stage = {"LAUNCHPAD_CREATED": "LAUNCHPAD_DISCOVERED", "PAIR_CREATED": "DEX_PAIR_DISCOVERED",
             "LAUNCHPAD_LIFECYCLE": "LAUNCHPAD_ACTIVE"}.get(proof.source_kind, "TRADING")
    if any(isinstance(row, TradeEvent) and row.launchpad for row, _ in valid):
        stage = "LAUNCHPAD_ACTIVE"
    if any(isinstance(row, TokenLifecycleEvent) and row.event_type == "GRADUATED" for row, _ in valid):
        stage = "LAUNCHPAD_GRADUATED"
    if any(isinstance(row, Pair) for row, _ in valid):
        stage = "DEX_PAIR_DISCOVERED"
    if any(isinstance(row, TokenLifecycleEvent) and row.event_type in {"MIGRATED", "MIGRATED_TO_DEX"} for row, _ in valid):
        stage = "MIGRATED_TO_DEX"
    return {"discovered_at": utc(observed(first)), "discovery_source": proof.source_kind,
            "first_seen_block": first.creation_block if isinstance(first, Pair) else first.block_number,
            "latest_seen_at": max(utc(observed(row)) for row, _ in valid), "stage": stage,
            "launchpad": next((row.launchpad for row, _ in reversed(valid)
                               if isinstance(row, (TradeEvent, TokenLifecycleEvent)) and row.launchpad), None)}


def build_projection(engine, candidate, *, at, config_hash=None, reconstruction_policy=None):
    session, at = engine.session, utc(at)
    token = session.get(Token, candidate.token_id)
    epoch = capture_epoch(session, token.chain)
    valid = canonical_proofs(engine, candidate, at=at)
    context = _context(valid) if valid else {}
    lineage = {"facts": {}, "dependencies": []}
    temporary = SimpleNamespace(id=candidate.id, token_id=token.id, invalidated_at=None,
                                smart_snapshot_id=None, risk_snapshot_id=None, latest_signal_id=None,
                                **context)
    features = engine.features(token, temporary, at, lineage=lineage) if valid else RadarFeatureSnapshot.unknown(
        age_seconds=0, market_phase=UNKNOWN,
    ).as_dict()
    if valid:
        context["latest_seen_at"] = temporary.latest_seen_at
    parameters = {"candidate_id": candidate.id, "as_of": at, "config": engine.config.model_dump(mode="json")}
    if reconstruction_policy:
        parameters["reconstruction_policy"] = reconstruction_policy
    lineage["facts"]["discovery"] = [
        {"source": row_values(row), "proof": {k: v for k, v in row_values(proof).items() if k != "valid"},
         "raw": row_values(raw) if (raw := engine.raw_for(row, token.chain)) is not None else None}
        for row, proof in valid
    ]
    if capture_epoch(session, token.chain) != epoch:
        raise StaleProjectionInputs("canonical epoch changed during Radar projection")
    return publish_snapshot(session, RadarFeatureObservation(
        chain=token.chain, token_address=token.address, candidate_id=candidate.id, as_of=at,
        config_hash=config_hash or digest(parameters["config"]), source_epoch=epoch,
        snapshot_status="VALID" if valid else "UNAVAILABLE", features_json=features,
        priority_json=priority(features, engine.config) if valid else {}, context_json=json_value(context),
        smart_snapshot_id=temporary.smart_snapshot_id, risk_snapshot_id=temporary.risk_snapshot_id,
        data_status=("DEGRADED" if UNKNOWN in features.values() else "OBSERVED") if valid else "ORPHANED",
        input_manifest=manifest(kind="radar_feature_observations", parameters=parameters,
                                facts=lineage["facts"], dependencies=lineage["dependencies"],
                                notes=[] if valid else ["NO_CANONICAL_EVIDENCE"]),
    ))


def projection_for(session, candidate):
    epoch = select(ResearchState.epoch).where(ResearchState.chain == RadarFeatureObservation.chain).scalar_subquery()
    return session.scalar(select(RadarFeatureObservation).join(
        RadarCandidate, RadarCandidate.projection_id == RadarFeatureObservation.id,
    ).where(RadarCandidate.id == candidate.id, RadarFeatureObservation.source_epoch == func.coalesce(epoch, 0),
            RadarFeatureObservation.snapshot_status.in_(("VALID", "UNAVAILABLE")))
        .execution_options(include_invalid_snapshots=True, populate_existing=True))


def apply_projection(engine, candidate, snapshot):
    session, at = engine.session, utc(snapshot.as_of)
    previous = session.scalar(select(RadarFeatureObservation).join(
        RadarCandidate, RadarCandidate.projection_id == RadarFeatureObservation.id,
    ).where(RadarCandidate.id == candidate.id).execution_options(include_invalid_snapshots=True))
    if previous is not None and (utc(previous.as_of) > at or previous.source_epoch > snapshot.source_epoch):
        return False
    if snapshot.source_epoch != capture_epoch(session, snapshot.chain):
        return False
    rebuilding = bool(session.info.get("research_rebuild_root"))
    event_at = datetime.now(UTC) if rebuilding else at

    def move(target, reason):
        engine.move(candidate, target, f"RESEARCH_REBUILD:{reason}" if rebuilding else reason, event_at)

    candidate.projection_id = snapshot.id
    candidate.features_json = snapshot.features_json if snapshot.snapshot_status == "VALID" else {}
    candidate.priority_json = snapshot.priority_json
    candidate.priority_score = Decimal(snapshot.priority_json.get("priority_score", 0))
    candidate.smart_snapshot_id, candidate.risk_snapshot_id = snapshot.smart_snapshot_id, snapshot.risk_snapshot_id
    candidate.data_status, candidate.updated_at = snapshot.data_status, at
    valid_ids = {item["proof"]["id"] for item in snapshot.input_manifest.get("facts", {}).get("discovery", [])}
    for proof in session.scalars(select(RadarCandidateEvidence).where(RadarCandidateEvidence.candidate_id == candidate.id)):
        proof.valid = proof.id in valid_ids
    if snapshot.snapshot_status != "VALID":
        move(S.REJECTED, "NO_CANONICAL_EVIDENCE")
        candidate.latest_signal_id = None
        candidate.stage = UNKNOWN
        session.flush()
        return True
    context = snapshot.context_json
    for field in ("discovered_at", "latest_seen_at"):
        setattr(candidate, field, datetime.fromisoformat(context[field]))
    for field in ("discovery_source", "first_seen_block", "stage"):
        setattr(candidate, field, context[field])
    signal = session.scalar(select(Signal).where(
        Signal.token_id == candidate.token_id, Signal.created_at <= at,
        Signal.model_version == engine.config.signal_version,
    ).order_by(Signal.created_at.desc(), Signal.research_revision.desc()).limit(1))
    candidate.latest_signal_id = signal.id if signal else None
    if (at - utc(candidate.latest_seen_at)).total_seconds() > engine.config.stale_seconds:
        if candidate.status == S.REJECTED:
            move(S.DISCOVERED, "CANONICAL_EVIDENCE_RESTORED")
        move(S.STALE, "ACTIVITY_EXPIRED")
    else:
        if candidate.status in {S.REJECTED, S.STALE}:
            move(S.DISCOVERED, "CANONICAL_EVIDENCE_RESTORED")
        if candidate.status == S.DISCOVERED:
            move(S.PREQUALIFIED, "CANONICAL_EVIDENCE_VALID")
        label = signal.signal_type.value if signal else UNKNOWN
        target = S.HIGH_RISK if snapshot.features_json["risk"] in {"HIGH", "CRITICAL"} else {
            "STRONG": S.HIGH_SIGNAL, "INTERESTING": S.INTERESTING, "HIGH_RISK": S.HIGH_RISK,
        }.get(label, S.WATCH)
        move(target, "LATEST_OBSERVED_FEATURES")
    session.flush()
    return True

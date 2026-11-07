"""Validate stored chain assertions against their captured epoch and exact facts."""

from datetime import datetime

from smms.db.models import RadarCandidate, RadarCandidateEvidence, Token
from smms.evaluation import utc
from smms.evidence.resolver import EvidenceResolver, normalized_evidence_level
from smms.research.lineage import digest, row_values
from smms.research.state import capture_epoch

from .models import EvidenceObservation


def is_chain(item):
    def value(key):
        return item.get(key) if isinstance(item, dict) else getattr(item, key, None)
    source_type = str(value("source_type") or "").upper()
    level = normalized_evidence_level(value("evidence_level"), source_type=source_type)
    return source_type in {"CHAIN", "ON_CHAIN", "RPC", "SOLANA_RPC", "BSC_RPC", "DERIVED", "DERIVED_CHAIN", "CHAIN_DERIVED"} or level in {
        "ON_CHAIN_VERIFIED", "DERIVED_FROM_CHAIN",
    }


def eligible(session, item, *, chain, token, as_of, epoch=None):
    if item.chain != chain or item.subject_address.lower() != token.lower():
        return False
    if utc(item.observed_at) > utc(as_of) or utc(item.fetched_at) > utc(as_of):
        return False
    if not is_chain(item):
        return True
    epoch = capture_epoch(session, chain) if epoch is None else epoch
    reference = item.raw_reference
    if not isinstance(reference, dict) or reference.get("research_epoch") != epoch:
        return False
    proof_id = reference.get("proof_id")
    if not isinstance(proof_id, int):
        return False
    proof = session.get(RadarCandidateEvidence, proof_id, populate_existing=True)
    candidate = session.get(RadarCandidate, proof.candidate_id) if proof else None
    subject = session.get(Token, candidate.token_id) if candidate else None
    if subject is None or subject.chain != chain or subject.address.lower() != token.lower():
        return False
    if reference.get("source_key") != proof.source_key or item.field != f"canonical_{proof.source_kind.lower()}" or item.value is not True:
        return False
    from smms.radar.config import RadarConfig
    from smms.radar.engine import SOURCES, RadarEngine, observed

    descriptor = SOURCES.get(proof.source_key.split(":")[0])
    if descriptor is None:
        return False
    model, column = descriptor
    row = session.get(model, getattr(proof, column), populate_existing=True)
    try:
        engine = RadarEngine(session, RadarConfig(**reference.get("radar_config", {})))
    except (TypeError, ValueError):
        return False
    if (not engine.canonical(row, chain, proof) or engine.source_kind(row) != proof.source_kind
            or utc(observed(row)) > utc(as_of)):
        return False
    raw = engine.raw_for(row, chain)
    return (reference.get("fact_digest") == digest(row_values(row))
            and reference.get("raw_digest") == (digest(row_values(raw)) if raw else None))


def canonical_observations(engine, candidate, *, as_of):
    from smms.radar.engine import RadarEngine, observed
    from smms.radar.projection import canonical_proofs, radar_config_for

    session = engine.session
    engine = RadarEngine(session, radar_config_for(session, candidate, engine.config), chain_id=engine.chain_id)
    token = session.get(Token, candidate.token_id)
    epoch = capture_epoch(session, token.chain)
    result = []
    for row, proof in canonical_proofs(engine, candidate, at=utc(as_of)):
        raw = engine.raw_for(row, token.chain)
        result.append(EvidenceObservation(
            chain=token.chain, subject_type="TOKEN", subject_address=token.address,
            field=f"canonical_{proof.source_kind.lower()}", value=True, source="smms_chain", source_type="CHAIN",
            observed_at=utc(observed(row)), fetched_at=utc(as_of), confidence=1.0, evidence_level="ON_CHAIN_VERIFIED",
            raw_reference={"research_epoch": epoch, "source_key": proof.source_key, "proof_id": proof.id,
                           "radar_config": engine.config.model_dump(mode="json"),
                           "fact_digest": digest(row_values(row)), "raw_digest": digest(row_values(raw)) if raw else None},
        ))
    return result if capture_epoch(session, token.chain) == epoch else []


def from_payload(payload):
    data = dict(payload)
    for key in ("observed_at", "fetched_at", "expires_at"):
        if data.get(key) is not None:
            data[key] = utc(datetime.fromisoformat(data[key]) if isinstance(data[key], str) else data[key])
    return EvidenceObservation(**data)


def filter_resolved(session, resolved, *, chain, token, as_of, epoch):
    result = {}
    for field, value in (resolved or {}).items():
        observations = getattr(value, "observations", ())
        if not observations and isinstance(value, dict):
            observations = value.get("observations", ())
        if observations:
            allowed = []
            for payload in observations:
                item = from_payload(payload)
                if eligible(session, item, chain=chain, token=token, as_of=as_of, epoch=epoch):
                    allowed.append(item)
            if allowed:
                result.update(EvidenceResolver().resolve(allowed, now=utc(as_of)))
        elif not is_chain(value):
            result[field] = value
    return result

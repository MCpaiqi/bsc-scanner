"""Materialize token risk snapshots from canonical project data."""
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from smms.chains.constants import normalize_address, validate_bsc_chain
from smms.db.facts import canonical_fact_conditions
from smms.db.models import Token, TokenRiskSnapshot, TradeEvent
from smms.db.repositories import TokenRiskSnapshotRepository
from smms.external_evidence.chain import filter_resolved
from smms.research.lineage import manifest, row_values
from smms.research.state import capture_epoch

from .engine import RISK_VERSION, analyze_token_risk


def _json_safe(value: Any) -> Any:
    """Convert numeric/time evidence to values accepted by JSON columns."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    enum_value = getattr(value, "value", None)
    if enum_value is not None and not isinstance(value, (str, bytes, int, float, bool)):
        return _json_safe(enum_value)
    return value


class RiskAggregationService:
    def __init__(self, session: Session, *, chain: str = "bsc", risk_version: str = RISK_VERSION) -> None:
        self.session = session
        self.chain = validate_bsc_chain(chain)
        self.risk_version = risk_version

    def refresh(
        self,
        *,
        as_of: datetime | None = None,
        token_address: str | None = None,
        resolved_evidence: Mapping[str, Any] | None = None,
        historical_rebuild: bool = False,
    ) -> int:
        cutoff = as_of or datetime.now(UTC)
        source_epoch = capture_epoch(self.session, self.chain)
        query = select(Token).where(Token.chain == self.chain)
        if token_address:
            query = query.where(Token.address == normalize_address(token_address, chain=self.chain))
        tokens = list(self.session.scalars(query).all())
        repo = TokenRiskSnapshotRepository(self.session)
        count = 0
        for token in tokens:
            trades = list(self.session.scalars(
                select(TradeEvent).where(
                    TradeEvent.chain == self.chain,
                    TradeEvent.token_address == token.address,
                    TradeEvent.timestamp <= cutoff,
                    *canonical_fact_conditions(TradeEvent),
                ).order_by(TradeEvent.timestamp, TradeEvent.id)
            ).all())
            # Token cache fields have no historical/fork identity. Explicit
            # resolved evidence and canonical trades are the reproducible inputs.
            observation = {"address": token.address, "metadata_json": {}}
            if historical_rebuild:
                from smms.evidence import EvidenceResolver
                from smms.external_evidence.orchestrator import ExternalEvidenceOrchestrator

                stored = ExternalEvidenceOrchestrator(self.session, providers=[]).load(self.chain, token.address, as_of=cutoff)
                evidence = EvidenceResolver().resolve(stored, now=cutoff)
            else:
                evidence = filter_resolved(
                    self.session, resolved_evidence, chain=self.chain, token=token.address, as_of=cutoff, epoch=source_epoch,
                )
            analysis = analyze_token_risk(
                observation,
                trades=trades,
                as_of=cutoff,
                resolved_evidence=evidence,
            )
            provenance = {
                name: item.as_dict() if hasattr(item, "as_dict") else dict(item)
                for name, item in (evidence or {}).items()
            }
            row = TokenRiskSnapshot(
                source_epoch=source_epoch,
                input_manifest=manifest(kind="token_risk_snapshots", parameters={
                    "token_address": token.address, "as_of": cutoff, "risk_version": self.risk_version,
                    "input_policy": "canonical-research-v1",
                }, facts={"token_observation": observation,
                           "trades": [row_values(trade) for trade in trades],
                           "resolved_evidence": _json_safe(provenance),
                           "evidence_sources": _json_safe({name: list(getattr(item, "observations", ()))
                                                           for name, item in (evidence or {}).items()})},
                    notes=["HISTORICAL_TOKEN_CACHE_UNAVAILABLE" if historical_rebuild else "UNVERSIONED_TOKEN_CACHE_UNAVAILABLE"]),
                chain=self.chain,
                token_address=analysis.token,
                contract_risk=analysis.contract_risk.level,
                liquidity_risk=analysis.liquidity_risk.level,
                holder_risk=analysis.holder_risk.level,
                behavior_risk=analysis.behavior_risk.level,
                bot_probability=analysis.bot_probability,
                wash_probability=analysis.wash_probability,
                overall_risk=analysis.overall_risk,
                confidence=analysis.confidence,
                evidence_json=analysis.evidence,
                dimensions_json=_json_safe({
                    "contract": analysis.contract_risk.as_dict(),
                    "liquidity": analysis.liquidity_risk.as_dict(),
                    "holder": analysis.holder_risk.as_dict(),
                    "behavior": analysis.behavior_risk.as_dict(),
                    "resolved_evidence": provenance,
                }),
                as_of=cutoff,
                risk_version=self.risk_version,
            )
            repo.upsert(row)
            count += 1
        return count

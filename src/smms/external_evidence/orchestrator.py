"""Production external-evidence orchestration and persistence."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from smms.db.models import EvidenceObservationRecord
from smms.evaluation import utc
from smms.evidence.resolver import EvidenceResolver, ResolvedEvidence
from smms.observability import source_health_registry

from .chain import eligible
from .models import EvidenceObservation
from .provider import Capability, ExternalEvidenceProvider

_METHODS = {
    Capability.TOKEN_MARKET: "fetch_token_market",
    Capability.TOKEN_SECURITY: "fetch_token_security",
    Capability.HOLDER_INFORMATION: "fetch_holder_information",
    Capability.SOCIAL_METADATA: "fetch_social_metadata",
}


class ExternalEvidenceOrchestrator:
    """Run configured providers and return one chain-first resolved snapshot."""

    def __init__(
        self,
        session: Session,
        *,
        providers: Sequence[ExternalEvidenceProvider],
        resolver: EvidenceResolver | None = None,
    ) -> None:
        self.session = session
        self.providers = tuple(providers)
        self.resolver = resolver or EvidenceResolver()

    async def refresh(
        self,
        chain: str,
        token_address: str,
        *,
        as_of: datetime | None = None,
        chain_observations: Iterable[EvidenceObservation] = (),
    ) -> dict[str, ResolvedEvidence]:
        cutoff, observations = await self._collect(
            chain, token_address, as_of=as_of, chain_observations=chain_observations
        )
        observations.extend(self.load(chain, token_address, as_of=cutoff))
        allowed = [item for item in observations if eligible(self.session, item, chain=chain, token=token_address, as_of=cutoff)]
        for item in allowed:
            self._persist(item)
        self.session.flush()
        return self.resolver.resolve(allowed, now=cutoff)

    async def _collect(
        self,
        chain: str,
        token_address: str,
        *,
        as_of: datetime | None = None,
        chain_observations: Iterable[EvidenceObservation] = (),
    ) -> tuple[datetime, list[EvidenceObservation]]:
        cutoff = as_of or datetime.now(UTC)
        cutoff = cutoff.replace(tzinfo=UTC) if cutoff.tzinfo is None else cutoff.astimezone(UTC)
        observations = list(chain_observations)
        for provider in self.providers:
            for capability in sorted(provider.capabilities, key=str):
                method = _METHODS.get(Capability(capability))
                if method is None:
                    continue
                try:
                    observations.extend(
                        await getattr(provider, method)(chain, token_address)
                    )
                except Exception as exc:  # noqa: BLE001 - optional providers are non-fatal
                    source_health_registry.record_failure(provider.provider_name, exc)
        return (cutoff if as_of is not None else datetime.now(UTC)), observations

    def refresh_sync(
        self,
        chain: str,
        token_address: str,
        *,
        as_of: datetime | None = None,
        chain_observations: Iterable[EvidenceObservation] = (),
    ) -> dict[str, ResolvedEvidence]:
        """Synchronous worker seam that keeps DB writes on the caller thread."""
        import asyncio

        async def collect() -> tuple[datetime, list[EvidenceObservation]]:
            return await self._collect(
                chain, token_address, as_of=as_of, chain_observations=chain_observations
            )

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            cutoff, observations = asyncio.run(collect())
        else:
            # AuditWorker runs inside AsyncSession.run_sync. Provider I/O may
            # remain async, but this sync SQLAlchemy Session must never cross
            # the event-loop thread boundary.
            with ThreadPoolExecutor(max_workers=1) as executor:
                cutoff, observations = executor.submit(asyncio.run, collect()).result()
        observations.extend(self.load(chain, token_address, as_of=cutoff))
        allowed = [item for item in observations if eligible(self.session, item, chain=chain, token=token_address, as_of=cutoff)]
        for item in allowed:
            self._persist(item)
        self.session.flush()
        return self.resolver.resolve(allowed, now=cutoff)

    def load(
        self, chain: str, token_address: str, *, as_of: datetime
    ) -> list[EvidenceObservation]:
        rows = self.session.scalars(
            select(EvidenceObservationRecord).where(
                EvidenceObservationRecord.chain == chain,
                EvidenceObservationRecord.subject_address == token_address,
                EvidenceObservationRecord.observed_at <= as_of,
                EvidenceObservationRecord.fetched_at <= as_of,
            ).execution_options(populate_existing=True)
        ).all()
        observations = [
            EvidenceObservation(
                chain=row.chain,
                subject_type=row.subject_type,
                subject_address=row.subject_address,
                field=row.field,
                value=row.value_json,
                source=row.source,
                source_type=row.source_type,
                observed_at=utc(row.observed_at),
                fetched_at=utc(row.fetched_at),
                confidence=float(row.confidence) if row.confidence is not None else None,
                evidence_level=row.evidence_level,
                raw_reference=row.raw_reference_json,
                expires_at=utc(row.expires_at) if row.expires_at else None,
                stale=row.stale,
            )
            for row in rows
        ]
        return [item for item in observations if eligible(self.session, item, chain=chain, token=token_address, as_of=as_of)]

    def _persist(self, item: EvidenceObservation) -> None:
        existing = self.session.scalar(
            select(EvidenceObservationRecord.id).where(
                EvidenceObservationRecord.chain == item.chain,
                EvidenceObservationRecord.subject_address == item.subject_address,
                EvidenceObservationRecord.field == item.field,
                EvidenceObservationRecord.source == item.source,
                EvidenceObservationRecord.fetched_at == item.fetched_at,
            )
        )
        if existing is not None:
            return
        self.session.add(
            EvidenceObservationRecord(
                chain=item.chain,
                subject_type=str(item.subject_type),
                subject_address=item.subject_address,
                field=item.field,
                value_json=item.value,
                source=item.source,
                source_type=item.source_type,
                observed_at=item.observed_at,
                fetched_at=item.fetched_at,
                confidence=item.confidence,
                evidence_level=str(item.evidence_level),
                raw_reference_json=item.raw_reference,
                expires_at=item.expires_at,
                stale=item.stale,
            )
        )


__all__ = ["ExternalEvidenceOrchestrator"]

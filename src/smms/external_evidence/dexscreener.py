"""DexScreener external evidence provider."""

from datetime import UTC, datetime
from typing import Any

from smms.evidence.providers import DexScreenerEvidenceProvider as _Provider

from .models import EvidenceLevel, EvidenceObservation, SubjectType
from .provider import Capability, ExternalEvidenceProviderBase


class DexScreenerProvider(ExternalEvidenceProviderBase):
    provider_name = "dexscreener"
    capabilities = frozenset({Capability.TOKEN_MARKET, Capability.SOCIAL_METADATA})

    def __init__(
        self,
        *,
        client: Any = None,
        base_url: str = "https://api.dexscreener.com",
        timeout: float = 10,
        ttl: float = 30,
        stale_ttl: float = 300,
        rate_limit: float = 0,
        enabled: bool = True,
        **kwargs: Any,
    ) -> None:
        chain = kwargs.pop("chain", "bsc")
        self.chain = chain
        self._provider = _Provider(
            chain=chain,
            client=client,
            base_url=base_url,
            timeout=timeout,
            ttl=ttl,
            stale_ttl=stale_ttl,
            rate_limit=rate_limit,
            enabled=enabled,
            **kwargs,
        )
        self.health = self._provider.health
        self._cache = self._provider._cache

    async def _convert(
        self, chain: str, address: str, capability: str
    ) -> list[EvidenceObservation]:
        self._provider.chain = chain
        values = await getattr(self._provider, f"fetch_{capability}")(address)
        now = datetime.now(UTC)
        return [
            EvidenceObservation(
                chain=chain,
                subject_type=SubjectType.TOKEN,
                subject_address=address,
                field=item.field,
                value=item.value,
                source=self.provider_name,
                source_type="secondary_api",
                observed_at=item.observed_at or now,
                fetched_at=item.fetched_at or now,
                confidence=item.confidence,
                evidence_level=EvidenceLevel(item.evidence_level)
                if item.evidence_level in EvidenceLevel._value2member_map_
                else EvidenceLevel.UNKNOWN,
                raw_reference=item.raw_reference,
                expires_at=item.expires_at,
                stale=item.stale,
            )
            for item in values
        ]

    async def fetch_token_market(self, chain_or_address: str, token_address: str | None = None):
        chain, address = (
            (self.chain, chain_or_address)
            if token_address is None
            else (chain_or_address, token_address)
        )
        return await self._convert(chain, address, "token_market")

    async def fetch_social_metadata(self, chain_or_address: str, token_address: str | None = None):
        chain, address = (
            (self.chain, chain_or_address)
            if token_address is None
            else (chain_or_address, token_address)
        )
        return await self._convert(chain, address, "social_metadata")


DexScreenerEvidenceProvider = DexScreenerProvider
__all__ = ["DexScreenerEvidenceProvider", "DexScreenerProvider"]

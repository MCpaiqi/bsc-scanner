from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from typing import Protocol, runtime_checkable

from .models import EvidenceObservation


class Capability(StrEnum):
    TOKEN_MARKET = "token_market"
    TOKEN_SECURITY = "token_security"
    HOLDER_INFORMATION = "holder_information"
    SOCIAL_METADATA = "social_metadata"


class UnsupportedCapability(NotImplementedError):
    """Raised when a provider does not advertise a requested capability."""


class ExternalEvidenceProviderBase:
    """Convenience base class for providers implementing a subset of capabilities."""

    provider_name = "unknown"
    capabilities: frozenset[Capability] = frozenset()

    @staticmethod
    def _unsupported(capability: Capability) -> None:
        raise UnsupportedCapability(f"provider does not support {capability.value}")

    async def fetch_token_market(self, chain: str, token_address: str) -> Sequence[EvidenceObservation]:
        self._unsupported(Capability.TOKEN_MARKET)

    async def fetch_token_security(self, chain: str, token_address: str) -> Sequence[EvidenceObservation]:
        self._unsupported(Capability.TOKEN_SECURITY)

    async def fetch_holder_information(self, chain: str, token_address: str) -> Sequence[EvidenceObservation]:
        self._unsupported(Capability.HOLDER_INFORMATION)

    async def fetch_social_metadata(self, chain: str, token_address: str) -> Sequence[EvidenceObservation]:
        self._unsupported(Capability.SOCIAL_METADATA)


@runtime_checkable
class ExternalEvidenceProvider(Protocol):
    """Common interface for optional, secondary data providers.

    Implementations may support only a subset of methods; unsupported methods
    should raise :class:`UnsupportedCapability` rather than fabricate UNKNOWN
    evidence.  Callers can inspect ``capabilities`` before invoking a method.
    """

    provider_name: str
    capabilities: frozenset[Capability]

    async def fetch_token_market(self, chain: str, token_address: str) -> Sequence[EvidenceObservation]: ...

    async def fetch_token_security(self, chain: str, token_address: str) -> Sequence[EvidenceObservation]: ...

    async def fetch_holder_information(self, chain: str, token_address: str) -> Sequence[EvidenceObservation]: ...

    async def fetch_social_metadata(self, chain: str, token_address: str) -> Sequence[EvidenceObservation]: ...

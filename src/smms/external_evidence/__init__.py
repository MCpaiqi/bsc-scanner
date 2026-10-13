"""Standardized external evidence layer and concrete providers.

Concrete HTTP providers are imported lazily to avoid a package initialization
cycle with the compatibility provider module in :mod:`smms.evidence`.
"""
from smms.evidence.resolver import (
    DEFAULT_TTLS,
    EvidenceResolver,
    Freshness,
    ResolvedEvidence,
    resolve_evidence,
)

from .models import EvidenceLevel, EvidenceObservation, SubjectType, sanitize_raw_reference
from .provider import (
    Capability,
    ExternalEvidenceProvider,
    ExternalEvidenceProviderBase,
    UnsupportedCapability,
)
from .resilience import (
    CacheLookup,
    ProviderHealth,
    ProviderRuntime,
    RateLimiter,
    TTLCache,
    backoff_delay,
)

__all__ = [
    "DEFAULT_TTLS",
    "CacheLookup",
    "Capability",
    "DexScreenerEvidenceProvider",
    "DexScreenerProvider",
    "EvidenceLevel",
    "EvidenceObservation",
    "EvidenceResolver",
    "ExternalEvidenceOrchestrator",
    "ExternalEvidenceProvider",
    "ExternalEvidenceProviderBase",
    "Freshness",
    "GoPlusEvidenceProvider",
    "GoPlusProvider",
    "ProviderHealth",
    "ProviderRuntime",
    "RateLimiter",
    "ResolvedEvidence",
    "SubjectType",
    "TTLCache",
    "UnsupportedCapability",
    "backoff_delay",
    "resolve_evidence",
    "sanitize_raw_reference",
]


def __getattr__(name: str):
    if name == "ExternalEvidenceOrchestrator":
        from .orchestrator import ExternalEvidenceOrchestrator

        return ExternalEvidenceOrchestrator
    if name in {"DexScreenerEvidenceProvider", "DexScreenerProvider"}:
        from .dexscreener import DexScreenerEvidenceProvider, DexScreenerProvider
        return DexScreenerEvidenceProvider if name == "DexScreenerEvidenceProvider" else DexScreenerProvider
    if name in {"GoPlusEvidenceProvider", "GoPlusProvider"}:
        from .goplus import GoPlusEvidenceProvider, GoPlusProvider
        return GoPlusEvidenceProvider if name == "GoPlusEvidenceProvider" else GoPlusProvider
    if name == "GoPlusAccessTokenManager":
        from .goplus import GoPlusAccessTokenManager
        return GoPlusAccessTokenManager
    raise AttributeError(name)

"""Deterministic resolution of chain and external evidence.

The resolver is deliberately side-effect free.  It chooses a value for each
field while retaining provenance, confidence, freshness and disagreements.
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from smms.external_evidence.models import EvidenceLevel, EvidenceObservation


class Freshness(StrEnum):
    FRESH = "FRESH"
    STALE = "STALE"
    EXPIRED = "EXPIRED"
    UNKNOWN = "UNKNOWN"


_RANK = {
    EvidenceLevel.UNKNOWN.value: 0,
    EvidenceLevel.UNVERIFIED_PROVIDER.value: 1,
    EvidenceLevel.TRUSTED_PROVIDER.value: 2,
    EvidenceLevel.DERIVED_FROM_CHAIN.value: 3,
    EvidenceLevel.ON_CHAIN_VERIFIED.value: 4,
}
EVIDENCE_PRIORITY = dict(_RANK)

# Conservative defaults: prices/concentration change quickly while code and
# social metadata are comparatively stable.
DEFAULT_TTLS: dict[str, timedelta] = {
    "price": timedelta(seconds=15),
    "price_usd": timedelta(seconds=15),
    "volume_5m": timedelta(seconds=30),
    "volume_1h": timedelta(minutes=2),
    "volume_h24": timedelta(minutes=5),
    "holder_concentration": timedelta(minutes=5),
    "top_holder_concentration": timedelta(minutes=5),
    "holders": timedelta(minutes=5),
    "holder_count": timedelta(minutes=5),
    "contract_code": timedelta(days=3650),
    "code_exists": timedelta(days=3650),
    "social_metadata": timedelta(days=7),
    "website": timedelta(days=7),
    "social_links": timedelta(days=7),
}
DEFAULT_TTL = timedelta(minutes=5)


def normalized_evidence_level(value: Any, *, source_type: str = "") -> str:
    raw = str(value or EvidenceLevel.UNKNOWN.value)
    aliases = {
        "DERIVED": EvidenceLevel.DERIVED_FROM_CHAIN.value,
        "INFERRED": EvidenceLevel.UNVERIFIED_PROVIDER.value,
        "VERIFIED": EvidenceLevel.ON_CHAIN_VERIFIED.value,
    }
    raw = aliases.get(raw, raw)
    if raw in _RANK:
        return raw
    if str(source_type).upper() in {"CHAIN", "ON_CHAIN", "RPC", "SOLANA_RPC", "BSC_RPC"}:
        return EvidenceLevel.ON_CHAIN_VERIFIED.value
    if str(source_type).upper() in {"DERIVED", "DERIVED_CHAIN", "CHAIN_DERIVED"}:
        return EvidenceLevel.DERIVED_FROM_CHAIN.value
    return EvidenceLevel.UNKNOWN.value


_level = normalized_evidence_level


def _equal(left: Any, right: Any) -> bool:
    # Provider payloads may use strings for booleans/numbers.  Keep strict
    # equality for auditability, but normalize obvious scalar representations.
    if isinstance(left, bool) or isinstance(right, bool):
        return str(left).lower() == str(right).lower()
    return left == right or str(left) == str(right)


@dataclass(frozen=True, slots=True)
class ResolvedEvidence:
    field: str
    resolved_value: Any
    resolution_source: str
    evidence_level: str
    confidence: float | None
    freshness: Freshness
    conflict: bool
    conflicting_sources: tuple[str, ...]
    observations: tuple[dict[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "value": self.resolved_value,
            "resolved_value": self.resolved_value,
            "source": self.resolution_source,
            "resolution_source": self.resolution_source,
            "evidence_level": self.evidence_level,
            "confidence": self.confidence,
            "freshness": self.freshness.value,
            "conflict": self.conflict,
            "conflicting_sources": list(self.conflicting_sources),
        }


class EvidenceResolver:
    """Resolve observations with an explicit chain-first evidence hierarchy."""

    def __init__(self, *, ttls: Mapping[str, timedelta] | None = None, default_ttl: timedelta = DEFAULT_TTL) -> None:
        self.ttls = {**DEFAULT_TTLS, **(ttls or {})}
        self.default_ttl = default_ttl

    def _freshness(self, observation: EvidenceObservation, *, now: datetime, ttl: timedelta) -> Freshness:
        if observation.value is None:
            return Freshness.UNKNOWN
        if observation.expires_at is not None:
            if now >= observation.expires_at:
                return Freshness.EXPIRED
            return Freshness.STALE if observation.stale else Freshness.FRESH
        if observation.stale:
            return Freshness.STALE
        age = max(0.0, (now - observation.fetched_at).total_seconds())
        seconds = ttl.total_seconds()
        if age <= seconds:
            return Freshness.FRESH
        if age <= seconds * 2:
            return Freshness.STALE
        return Freshness.EXPIRED

    def resolve(self, observations: Iterable[EvidenceObservation], *, now: datetime | None = None) -> dict[str, ResolvedEvidence]:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        grouped: dict[str, list[EvidenceObservation]] = {}
        for item in observations:
            if isinstance(item, Mapping):
                payload = dict(item)
                for key in ("observed_at", "fetched_at", "expires_at"):
                    value = payload.get(key)
                    if isinstance(value, str):
                        payload[key] = datetime.fromisoformat(value)
                item = EvidenceObservation(**payload)
            grouped.setdefault(item.field, []).append(item)
        result: dict[str, ResolvedEvidence] = {}
        for field, values in grouped.items():
            latest = {}
            for item in values:
                key = (item.chain, item.subject_type, item.subject_address, item.source)
                previous = latest.get(key)
                order = lambda value: (value.fetched_at, value.observed_at, json.dumps(value.as_dict(), sort_keys=True, default=str))
                if previous is None or order(item) > order(previous):
                    latest[key] = item
            values = list(latest.values())
            ranked = sorted(values, key=lambda item: (_RANK.get(_level(item.evidence_level, source_type=item.source_type), 0),
                                                       item.fetched_at, item.observed_at, item.source), reverse=True)
            winner = next((item for item in ranked if item.value is not None), ranked[0])
            winner_level = _level(winner.evidence_level, source_type=winner.source_type)
            non_null = [item for item in values if item.value is not None]
            conflict_sources = tuple(dict.fromkeys(item.source for item in non_null if not _equal(item.value, winner.value)))
            freshness = self._freshness(winner, now=current, ttl=self.ttls.get(field, self.default_ttl))
            result[field] = ResolvedEvidence(
                field=field,
                resolved_value=winner.value,
                resolution_source=winner.source,
                evidence_level=winner_level,
                confidence=winner.confidence,
                freshness=freshness,
                conflict=bool(conflict_sources),
                conflicting_sources=conflict_sources,
                observations=tuple(item.as_dict() for item in values),
            )
        return result

    def summary(self, observations: Iterable[EvidenceObservation], *, now: datetime | None = None) -> dict[str, dict[str, Any]]:
        return {field: value.as_dict() for field, value in self.resolve(observations, now=now).items()}


def resolve_evidence(observations: Iterable[EvidenceObservation], *, now: datetime | None = None, ttls: Mapping[str, timedelta] | None = None) -> dict[str, ResolvedEvidence]:
    return EvidenceResolver(ttls=ttls).resolve(observations, now=now)


__all__ = ["DEFAULT_TTLS", "EVIDENCE_PRIORITY", "EvidenceResolver", "Freshness", "ResolvedEvidence", "resolve_evidence"]

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class SubjectType(StrEnum):
    TOKEN = "TOKEN"
    PAIR = "PAIR"
    CONTRACT = "CONTRACT"


class EvidenceLevel(StrEnum):
    """Auditable provenance for chain, derived, and provider observations."""

    ON_CHAIN_VERIFIED = "ON_CHAIN_VERIFIED"
    DERIVED_FROM_CHAIN = "DERIVED_FROM_CHAIN"
    TRUSTED_PROVIDER = "TRUSTED_PROVIDER"
    UNVERIFIED_PROVIDER = "UNVERIFIED_PROVIDER"
    UNKNOWN = "UNKNOWN"
    # Backwards-compatible spellings used by earlier ingestion code.
    DERIVED = "DERIVED"
    INFERRED = "INFERRED"


_SENSITIVE = re.compile(r"(?:api[_-]?key|authorization|auth|credential|password|secret|token)", re.IGNORECASE)
_MAX_RAW_BYTES = 4096


def sanitize_raw_reference(value: Any, *, max_bytes: int = _MAX_RAW_BYTES) -> Any:
    """Return bounded, credential-free metadata suitable for audit storage.

    This intentionally keeps only JSON-like values and truncates deterministically;
    full vendor payloads should remain outside the database.
    """

    def clean(item: Any, depth: int = 0) -> Any:
        if depth > 6:
            return "[TRUNCATED]"
        if isinstance(item, Mapping):
            return {str(k): clean(v, depth + 1) for k, v in item.items() if not _SENSITIVE.search(str(k))}
        if isinstance(item, (list, tuple)):
            return [clean(v, depth + 1) for v in item[:100]]
        if isinstance(item, (str, int, float, bool)) or item is None:
            return item
        return str(item)

    cleaned = clean(value)
    try:
        encoded = json.dumps(cleaned, ensure_ascii=True, separators=(",", ":")).encode()
    except (TypeError, ValueError):
        encoded = b'"[UNSERIALIZABLE]"'
        cleaned = "[UNSERIALIZABLE]"
    if len(encoded) <= max_bytes:
        return cleaned
    # Keep a valid bounded JSON value and make truncation auditable.
    if isinstance(cleaned, str):
        budget = max(0, max_bytes - 32)
        return cleaned[:budget] + "...[TRUNCATED]"
    return {"truncated": True, "sha256": __import__("hashlib").sha256(encoded).hexdigest(), "bytes": len(encoded)}


def _aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class EvidenceObservation:
    chain: str
    subject_type: SubjectType | str
    subject_address: str
    field: str
    value: Any
    source: str
    source_type: str = "SECONDARY_PROVIDER"
    observed_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    confidence: float | None = None
    evidence_level: EvidenceLevel | str = EvidenceLevel.UNKNOWN
    raw_reference: Any = None
    expires_at: datetime | None = None
    stale: bool = False

    def __post_init__(self) -> None:
        if not self.chain or not self.subject_address or not self.field or not self.source:
            raise ValueError("chain, subject_address, field, and source are required")
        if self.confidence is not None and not 0 <= float(self.confidence) <= 1:
            raise ValueError("confidence must be between 0 and 1")
        object.__setattr__(self, "observed_at", _aware(self.observed_at, "observed_at"))
        object.__setattr__(self, "fetched_at", _aware(self.fetched_at, "fetched_at"))
        if self.expires_at is not None:
            object.__setattr__(self, "expires_at", _aware(self.expires_at, "expires_at"))
        object.__setattr__(self, "raw_reference", sanitize_raw_reference(self.raw_reference))
        object.__setattr__(self, "subject_type", str(self.subject_type))
        object.__setattr__(self, "evidence_level", str(self.evidence_level))

    @property
    def is_stale(self) -> bool:
        return self.stale or (self.expires_at is not None and datetime.now(UTC) >= self.expires_at)

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        for key in ("observed_at", "fetched_at", "expires_at"):
            if result[key] is not None:
                result[key] = result[key].isoformat()
        return result

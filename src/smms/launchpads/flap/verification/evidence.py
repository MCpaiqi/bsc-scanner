"""Evidence levels used by the Flap verification and ingestion layers."""

from enum import Enum


class EvidenceLevel(str, Enum):
    """Strength of an on-chain fact or a value derived from one."""

    VERIFIED = "VERIFIED"
    DERIVED = "DERIVED"
    INFERRED = "INFERRED"
    UNKNOWN = "UNKNOWN"


__all__ = ["EvidenceLevel"]

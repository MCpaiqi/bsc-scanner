"""Verification metadata for the currently known Flap event ABI surface."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .events import (
    CANDIDATE_FLAP_EVENTS,
    TOKEN_BOUGHT,
    TOKEN_CREATED,
    TOKEN_SOLD,
    VerifiedFlapEvent,
)


@dataclass(frozen=True)
class VerifiedFlapABI:
    """A partial ABI register; full contract JSON is intentionally absent."""

    events: Mapping[str, VerifiedFlapEvent]
    full_abi_status: str = "PARTIAL: EVENT DECLARATIONS ONLY"

    @classmethod
    def current(cls) -> "VerifiedFlapABI":
        return cls(
            events=MappingProxyType(
                {
                    **dict(CANDIDATE_FLAP_EVENTS),
                    TOKEN_CREATED.name: TOKEN_CREATED,
                    TOKEN_BOUGHT.name: TOKEN_BOUGHT,
                    TOKEN_SOLD.name: TOKEN_SOLD,
                }
            )
        )

    def event(self, name: str) -> VerifiedFlapEvent:
        return self.events[name]


VERIFIED_FLAP_ABI = VerifiedFlapABI.current()

__all__ = ["VERIFIED_FLAP_ABI", "VerifiedFlapABI"]

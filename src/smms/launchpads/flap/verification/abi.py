"""当前已知 Flap 事件 ABI 的验证元数据。"""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .events import (
    CANDIDATE_FLAP_EVENTS,
    KNOWN_FLAP_EVENTS,
    VerifiedFlapEvent,
)


@dataclass(frozen=True)
class VerifiedFlapABI:
    """A partial ABI register; full contract JSON is intentionally absent."""

    events: Mapping[str, VerifiedFlapEvent]
    full_abi_status: str = "PARTIAL: EVENT DECLARATIONS ONLY"
    on_chain_verified: bool = False

    @classmethod
    def current(cls) -> "VerifiedFlapABI":
        return cls(
            events=MappingProxyType(
                {
                    **dict(CANDIDATE_FLAP_EVENTS),
                    **dict(KNOWN_FLAP_EVENTS),
                }
            )
        )

    def event(self, name: str) -> VerifiedFlapEvent:
        return self.events[name]


VERIFIED_FLAP_ABI = VerifiedFlapABI.current()

__all__ = ["VERIFIED_FLAP_ABI", "VerifiedFlapABI"]

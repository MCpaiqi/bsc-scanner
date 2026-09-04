"""Machine-readable verification report for Flap configuration review."""

from dataclasses import dataclass
from typing import Any

from .addresses import VerifiedFlapContracts
from .curve import verified_curve_facts
from .events import CANDIDATE_FLAP_EVENTS, VERIFIED_FLAP_EVENTS


@dataclass(frozen=True)
class FlapVerificationReport:
    contracts: VerifiedFlapContracts
    verified_events: tuple[str, ...]
    candidate_events: tuple[str, ...]
    curve_status: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "chain_id": self.contracts.chain_id,
            "contracts": {
                "portal": self.contracts.portal.address,
                "factory": self.contracts.factory.address if self.contracts.factory else None,
                "router": self.contracts.router.address if self.contracts.router else None,
                "bonding_curve": (
                    self.contracts.bonding_curve.address
                    if self.contracts.bonding_curve
                    else None
                ),
            },
            "verified_events": list(self.verified_events),
            "candidate_events": list(self.candidate_events),
            "curve": {
                "status": self.curve_status,
                "state_method": verified_curve_facts().state_method,
            },
        }


def build_flap_verification_report() -> FlapVerificationReport:
    return FlapVerificationReport(
        contracts=VerifiedFlapContracts.bsc_mainnet(),
        verified_events=tuple(VERIFIED_FLAP_EVENTS),
        candidate_events=tuple(CANDIDATE_FLAP_EVENTS),
        curve_status="PARTIAL: PER-TOKEN STATE REQUIRED",
    )


__all__ = ["FlapVerificationReport", "build_flap_verification_report"]

"""General curve facts verified from Flap documentation."""

from dataclasses import dataclass
from decimal import Decimal

from .evidence import EvidenceLevel


@dataclass(frozen=True)
class FlapCurveFacts:
    maximum_supply: Decimal
    token_decimals: int
    formula: str
    parameter_names: tuple[str, ...]
    state_method: str
    global_parameters: None = None
    evidence_level: EvidenceLevel = EvidenceLevel.VERIFIED
    source: str = "https://docs.flap.sh/flap/developers/wallet-and-terminal-and-bot-developers/bonding-curve-in-developers-perspective.md"


def verified_curve_facts() -> FlapCurveFacts:
    return FlapCurveFacts(
        maximum_supply=Decimal(1000000000),
        token_decimals=18,
        formula="constant-product",
        parameter_names=("r", "h", "k"),
        state_method="getTokenV8",
    )


__all__ = ["FlapCurveFacts", "verified_curve_facts"]

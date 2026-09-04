"""已验证的 Flap 合约地址记录。"""

import re
from dataclasses import dataclass

from .evidence import EvidenceLevel

_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


@dataclass(frozen=True)
class VerifiedAddress:
    role: str
    chain_id: int
    address: str
    source: str
    verified_at: str
    verification_method: str
    evidence_level: EvidenceLevel = EvidenceLevel.VERIFIED

    def __post_init__(self) -> None:
        if self.chain_id != 56:
            raise ValueError("Flap verification currently supports BNB mainnet chain id 56")
        if not _ADDRESS_RE.fullmatch(self.address):
            raise ValueError("address must be a 20-byte EVM address")
        object.__setattr__(self, "address", self.address)


@dataclass(frozen=True)
class VerifiedFlapContracts:
    """Contract roles with unknown roles represented by ``None``."""

    chain_id: int
    portal: VerifiedAddress
    factory: VerifiedAddress | None = None
    router: VerifiedAddress | None = None
    bonding_curve: VerifiedAddress | None = None
    bonding_curve_factory: VerifiedAddress | None = None

    @classmethod
    def bsc_mainnet(cls) -> "VerifiedFlapContracts":
        return cls(
            chain_id=56,
            portal=VerifiedAddress(
                role="portal",
                chain_id=56,
                address="0xe2cE6ab80874Fa9Fa2aAE65D277Dd6B8e65C9De0",
                source="https://docs.flap.sh/flap/developers/deployed-contract-addresses",
                verified_at="2026-09-04",
                verification_method="official deployed contract address list",
            ),
        )

    def require(self, role: str) -> VerifiedAddress:
        value = getattr(self, role, None)
        if value is None:
            raise ValueError(f"Flap contract role {role} is BLOCKED: VERIFY_ON_CHAIN")
        return value


__all__ = ["VerifiedAddress", "VerifiedFlapContracts"]

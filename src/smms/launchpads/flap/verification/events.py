"""已验证及候选 Flap 事件声明。

事件目录刻意保持为纯数据。它只记录已经验证的事实；未知的合约角色和
生命周期语义不会写入可启用的适配器配置。
"""

from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from types import MappingProxyType

from .evidence import EvidenceLevel


def keccak_topic(signature: str) -> str:
    """Return the Ethereum Keccak-256 event topic for a canonical signature."""

    if not isinstance(signature, str) or not signature.strip():
        raise ValueError("event signature is required")
    try:
        from Crypto.Hash import keccak
    except ImportError as exc:  # pragma: no cover - dependency availability is environment-specific
        raise RuntimeError("an Ethereum Keccak implementation is required") from exc
    digest = keccak.new(digest_bits=256)
    digest.update(signature.strip().encode("ascii"))
    return "0x" + digest.hexdigest()


@dataclass(frozen=True)
class VerifiedFlapEvent:
    """An event signature plus the field selectors confirmed by evidence."""

    name: str
    signature: str
    topic0: str
    data_indexes: Mapping[str, int]
    indexed_fields: tuple[str, ...] = ()
    indexed_topic_indexes: Mapping[str, int] = dataclass_field(default_factory=dict)
    evidence_level: EvidenceLevel = EvidenceLevel.VERIFIED
    source: str = ""
    verified_at: str = "2026-09-04"
    fixture_status: str = "PENDING"

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.signature.strip():
            raise ValueError("event name and signature are required")
        topic = self.topic0.strip().lower()
        if len(topic) != 66 or not topic.startswith("0x"):
            raise ValueError("topic0 must be a 32-byte hex value")
        try:
            int("0x" + topic[2:], 0)
        except ValueError as exc:
            raise ValueError("topic0 must be hexadecimal") from exc
        indexes: dict[str, int] = {}
        for field, index in self.data_indexes.items():
            if isinstance(index, bool) or not isinstance(index, int) or index < 0:
                raise ValueError(f"data index for {field} must be non-negative")
            indexes[str(field)] = index
        object.__setattr__(self, "topic0", topic)
        object.__setattr__(self, "data_indexes", MappingProxyType(indexes))
        indexed_fields = tuple(str(field) for field in self.indexed_fields)
        topic_indexes: dict[str, int] = {}
        for field, index in self.indexed_topic_indexes.items():
            if isinstance(index, bool) or not isinstance(index, int) or index < 1:
                raise ValueError(f"indexed topic index for {field} must be at least 1")
            topic_indexes[str(field)] = index
        if set(indexed_fields) != set(topic_indexes):
            raise ValueError(
                "indexed_fields and indexed_topic_indexes must describe the same fields"
            )
        overlap = set(indexed_fields).intersection(indexes)
        if overlap:
            raise ValueError(f"indexed fields cannot also be data fields: {sorted(overlap)}")
        object.__setattr__(self, "indexed_fields", indexed_fields)
        object.__setattr__(self, "indexed_topic_indexes", MappingProxyType(topic_indexes))

    def matches_topic(self, topic: str) -> bool:
        return isinstance(topic, str) and topic.strip().lower() == self.topic0


TOKEN_CREATED = VerifiedFlapEvent(
    name="TokenCreated",
    signature="TokenCreated(uint256,address,uint256,address,string,string,string)",
    topic0="0x504e7f360b2e5fe33cbaaae4c593bc55305328341bf79009e43e0e3b7f699603",
    data_indexes={"creator": 1, "token": 3},
    source="https://docs.flap.sh/flap/developers/wallet-and-terminal-and-bot-developers/index-token-created-events.md",
    fixture_status="OBSERVED_PENDING_FIXTURE",
)

TOKEN_BOUGHT = VerifiedFlapEvent(
    name="TokenBought",
    signature="TokenBought(uint256,address,address,uint256,uint256,uint256,uint256)",
    topic0="0xa800a2038683844fac66747f771bfdfae862eb28b16bcfa387afa9fbacce8ff7",
    data_indexes={
        "timestamp": 0,
        "token": 1,
        "trader": 2,
        "token_amount": 3,
        "native_amount": 4,
        "fee": 5,
        "price": 6,
    },
    source="https://docs.flap.sh/flap/developers/wallet-and-terminal-and-bot-developers/trade-tokens.md",
    fixture_status="ON_CHAIN_FIXTURE_REQUIRED",
)

TOKEN_SOLD = VerifiedFlapEvent(
    name="TokenSold",
    signature="TokenSold(uint256,address,address,uint256,uint256,uint256,uint256)",
    topic0="0x03a4693e592f5e75dc7c136acb39b146d2b4966c0e509c34f362dee02b3b861a",
    data_indexes={
        "timestamp": 0,
        "token": 1,
        "trader": 2,
        "token_amount": 3,
        "native_amount": 4,
        "fee": 5,
        "price": 6,
    },
    source="https://docs.flap.sh/flap/developers/wallet-and-terminal-and-bot-developers/trade-tokens.md",
    fixture_status="ON_CHAIN_FIXTURE_REQUIRED",
)


VERIFIED_FLAP_EVENTS: Mapping[str, VerifiedFlapEvent] = MappingProxyType(
    {
        TOKEN_CREATED.name: TOKEN_CREATED,
        TOKEN_BOUGHT.name: TOKEN_BOUGHT,
        TOKEN_SOLD.name: TOKEN_SOLD,
    }
)


def _candidate(name: str, signature: str, topic0: str) -> VerifiedFlapEvent:
    return VerifiedFlapEvent(
        name=name,
        signature=signature,
        topic0=topic0,
        data_indexes={},
        evidence_level=EvidenceLevel.DERIVED,
        source="computed from candidate signature; role and layout require on-chain verification",
        fixture_status="BLOCKED: VERIFY_ON_CHAIN",
    )


CANDIDATE_FLAP_EVENTS: Mapping[str, VerifiedFlapEvent] = MappingProxyType(
    {
        "LaunchedToDEX": _candidate(
            "LaunchedToDEX",
            "LaunchedToDEX(address,address,uint256,uint256)",
            "0x6e4f47630b8745b8cacbd44f42a8a33e7eea7cc08ef22fc7630f4f385784ff7d",
        ),
        "FlapTokenProgressChanged": _candidate(
            "FlapTokenProgressChanged",
            "FlapTokenProgressChanged(address,uint256)",
            "0x4c35e20d1e9bce377c7d9ec1572d934e46d62961f4da8af5beb8002d5906742d",
        ),
        "FlapTokenCirculatingSupplyChanged": _candidate(
            "FlapTokenCirculatingSupplyChanged",
            "FlapTokenCirculatingSupplyChanged(address,uint256)",
            "0x115c78ad17c4763fb97bca94f3e59dc8cb2e59c9d3862f24a694ec401200f562",
        ),
        "TokenCurveSetV2": _candidate(
            "TokenCurveSetV2",
            "TokenCurveSetV2(address,uint256,uint256,uint256)",
            "0x71a10912a55f73d3cced0d1515c2b33c396c80342522bad0e295ccbede556f37",
        ),
        "TokenDexSupplyThreshSet": _candidate(
            "TokenDexSupplyThreshSet",
            "TokenDexSupplyThreshSet(address,uint256)",
            "0x6f10bbe11587431707df676556a0551025a4f66551acdddc5793389e20f0d46e",
        ),
        "TokenQuoteSet": _candidate(
            "TokenQuoteSet",
            "TokenQuoteSet(address,address)",
            "0x3ceb902d3c555c21c3415b6aa839104b18e4825b2f8324011ff979089a507a8c",
        ),
    }
)


__all__ = [
    "CANDIDATE_FLAP_EVENTS",
    "TOKEN_BOUGHT",
    "TOKEN_CREATED",
    "TOKEN_SOLD",
    "VERIFIED_FLAP_EVENTS",
    "VerifiedFlapEvent",
    "keccak_topic",
]

"""Extensible EVM DEX protocol adapters.

Adapters own event selectors and ABI layouts. Ingestion only stores raw logs.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal

from Crypto.Hash import keccak

from smms.chains.base import ChainLog
from smms.chains.constants import BSC_CHAIN_ID

from .models import MarketEventRecord, PairRecord, TradeSide


def _hex_word(data: str, index: int) -> int:
    payload = data.removeprefix("0x")
    start = index * 64
    word = payload[start : start + 64]
    if len(word) != 64:
        raise ValueError("malformed ABI data")
    return int(word, 16)


def _topic_word(topics: tuple[str, ...], index: int) -> int:
    if index >= len(topics):
        raise ValueError("missing indexed topic")
    return int(topics[index], 16)


def _address(value: int) -> str:
    return f"0x{value & ((1 << 160) - 1):040x}"


def canonical_event_topic(signature: str) -> str:
    """Return topic0 for an EVM event's canonical signature."""
    digest = keccak.new(digest_bits=256)
    digest.update(signature.encode("utf-8"))
    return f"0x{digest.hexdigest()}"


def _topic(signature: str) -> str:
    return signature.lower()


class ProtocolAdapter(ABC):
    name: str
    chain_id: int = BSC_CHAIN_ID

    @abstractmethod
    def detect(self, log: ChainLog) -> bool: ...

    @abstractmethod
    def decode_pair_created(self, log: ChainLog) -> PairRecord | None: ...

    @abstractmethod
    def decode_swap(self, log: ChainLog) -> MarketEventRecord | None: ...

    @abstractmethod
    def decode_liquidity(self, log: ChainLog) -> MarketEventRecord | None: ...

    def normalize(self, log: ChainLog) -> tuple[PairRecord | None, MarketEventRecord | None]:
        if not self.detect(log):
            return None, None
        return self.decode_pair_created(log), self.decode_swap(log) or self.decode_liquidity(log)


@dataclass(frozen=True)
class PancakeSwapConfig:
    factory_addresses: tuple[str, ...] = ()
    quote_assets: tuple[str, ...] = (
        "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c",  # WBNB
        "0x55d398326f99059ff775485246999027b3197955",  # USDT
        "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d",  # USDC
        "0xe9e7cea3dedca5984780bafc599bd69add087d56",  # BUSD
    )
    pair_created_topic: str = canonical_event_topic("PairCreated(address,address,address,uint256)")
    swap_topic: str = canonical_event_topic("Swap(address,uint256,uint256,uint256,uint256,address)")
    sync_topic: str = canonical_event_topic("Sync(uint112,uint112)")
    mint_topic: str = canonical_event_topic("Mint(address,uint256,uint256)")
    burn_topic: str = canonical_event_topic("Burn(address,uint256,uint256,address)")


class PancakeSwapAdapter(ProtocolAdapter):
    name = "pancakeswap"

    def __init__(self, config: PancakeSwapConfig | None = None) -> None:
        self.config = config or PancakeSwapConfig()
        expected = {
            "pair_created_topic": canonical_event_topic("PairCreated(address,address,address,uint256)"),
            "swap_topic": canonical_event_topic("Swap(address,uint256,uint256,uint256,uint256,address)"),
            "sync_topic": canonical_event_topic("Sync(uint112,uint112)"),
            "mint_topic": canonical_event_topic("Mint(address,uint256,uint256)"),
            "burn_topic": canonical_event_topic("Burn(address,uint256,uint256,address)"),
        }
        for name, topic in expected.items():
            configured = getattr(self.config, name)
            if configured.lower() == topic.lower() and len(configured) == 66:
                continue
            # Custom topics are supported only as explicit, well-formed hashes;
            # reject known placeholders and malformed selectors at the boundary.
            value = configured.lower()
            if len(value) != 66 or not value.startswith("0x") or any(ch not in "0123456789abcdef" for ch in value[2:]):
                raise ValueError(f"{name} must be a verified 32-byte topic0")
            if "0011223344" in value or "f3f3f3f3" in value:
                raise ValueError(f"{name} contains a placeholder topic")
        self._topics = {
            _topic(self.config.pair_created_topic),
            _topic(self.config.swap_topic),
            _topic(self.config.sync_topic),
            _topic(self.config.mint_topic),
            _topic(self.config.burn_topic),
        }

    def detect(self, log: ChainLog) -> bool:
        if not log.topics or _topic(log.topics[0]) not in self._topics:
            return False
        if _topic(log.topics[0]) == _topic(self.config.pair_created_topic):
            return not self.config.factory_addresses or log.address.lower() in {
                item.lower() for item in self.config.factory_addresses
            }
        return True

    def decode_pair_created(self, log: ChainLog) -> PairRecord | None:
        if not log.topics or _topic(log.topics[0]) != _topic(self.config.pair_created_topic):
            return None
        try:
            token0 = _address(_topic_word(log.topics, 1))
            token1 = _address(_topic_word(log.topics, 2))
            pair = _address(_hex_word(log.data, 0))
            timestamp = log.timestamp
        except (ValueError, TypeError):
            return None
        quotes = {asset.lower() for asset in self.config.quote_assets}
        quote = token0 if token0.lower() in quotes else token1 if token1.lower() in quotes else None
        return PairRecord(
            chain_id=self.chain_id,
            address=pair,
            token0=token0,
            token1=token1,
            factory=log.address,
            dex=self.name,
            quote_token=quote,
            creation_tx_hash=log.transaction_hash,
            creation_block=log.block_number,
            creation_timestamp=timestamp,
            creation_block_hash=log.block_hash,
        )

    def decode_swap(self, log: ChainLog) -> MarketEventRecord | None:
        if not log.topics or _topic(log.topics[0]) != _topic(self.config.swap_topic):
            return None
        try:
            trader = _address(_topic_word(log.topics, 1))
            amount0_in, amount1_in = _hex_word(log.data, 0), _hex_word(log.data, 1)
            amount0_out, amount1_out = _hex_word(log.data, 2), _hex_word(log.data, 3)
        except (ValueError, TypeError):
            return None
        recipient = _address(_topic_word(log.topics, 2)) if len(log.topics) > 2 else None
        token_amount = Decimal(max(amount0_out, amount1_out, amount0_in, amount1_in))
        quote_amount = Decimal(min(amount0_out + amount1_out, amount0_in + amount1_in))
        return MarketEventRecord(
            event_type="TRADE",
            chain_id=self.chain_id,
            token=None,
            pair=log.address,
            wallet=trader,
            tx_hash=log.transaction_hash,
            block=log.block_number,
            timestamp=log.timestamp,
            side=TradeSide.UNKNOWN,
            token_amount=token_amount,
            quote_amount=quote_amount,
            source=f"bsc:{self.name}",
            confidence=Decimal("0.20"),
            metadata={
                "topic0": log.topics[0],
                "amounts_raw": [amount0_in, amount1_in, amount0_out, amount1_out],
                "swap_sender": trader,
                "swap_recipient": recipient,
                "trader_evidence": "swap_indexed_participants",
            },
        )

    def decode_liquidity(self, log: ChainLog) -> MarketEventRecord | None:
        if not log.topics:
            return None
        topic = _topic(log.topics[0])
        if topic not in {_topic(self.config.mint_topic), _topic(self.config.burn_topic), _topic(self.config.sync_topic)}:
            return None
        return MarketEventRecord(
            event_type=("LIQUIDITY_ADD" if topic == _topic(self.config.mint_topic) else "LIQUIDITY_REMOVE" if topic == _topic(self.config.burn_topic) else "SYNC"),
            chain_id=self.chain_id,
            token=None,
            pair=log.address,
            wallet=_address(_topic_word(log.topics, 2)) if len(log.topics) > 2 else None,
            tx_hash=log.transaction_hash,
            block=log.block_number,
            timestamp=log.timestamp,
            source=f"bsc:{self.name}",
            confidence=Decimal("0.90"),
            metadata={"topic0": log.topics[0]},
        )


class FlapProtocolAdapter(ProtocolAdapter):
    """Reserved adapter seam for verified Flap events.

    Flap's launchpad decoder remains the source of truth until emitter and ABI
    configuration is verified. This adapter therefore stays inert by default.
    """

    name = "flap"

    def __init__(self, selectors: tuple[str, ...] = ()) -> None:
        self.selectors = frozenset(item.lower() for item in selectors)

    def detect(self, log: ChainLog) -> bool:
        return bool(log.topics) and log.topics[0].lower() in self.selectors

    def decode_pair_created(self, log: ChainLog) -> PairRecord | None:
        return None

    def decode_swap(self, log: ChainLog) -> MarketEventRecord | None:
        return None

    def decode_liquidity(self, log: ChainLog) -> MarketEventRecord | None:
        return None


class ProtocolRegistry:
    def __init__(self, adapters: tuple[ProtocolAdapter, ...] | None = None) -> None:
        self.adapters = adapters or (PancakeSwapAdapter(),)

    def detect(self, log: ChainLog) -> ProtocolAdapter | None:
        return next((adapter for adapter in self.adapters if adapter.detect(log)), None)

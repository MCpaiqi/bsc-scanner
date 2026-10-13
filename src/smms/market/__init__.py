"""BSC market discovery and protocol-neutral event normalization."""

from .metadata import enrich_erc20_metadata
from .models import MarketEventRecord, PairRecord, TokenRecord, TradeSide
from .pipeline import BSCMarketPipeline
from .protocols import (
    FlapProtocolAdapter,
    PancakeSwapAdapter,
    ProtocolAdapter,
    ProtocolRegistry,
    canonical_event_topic,
)
from .snapshots import materialize_dexscreener_snapshot, materialize_trade_snapshot

__all__ = [
    "BSCMarketPipeline",
    "FlapProtocolAdapter",
    "MarketEventRecord",
    "PairRecord",
    "PancakeSwapAdapter",
    "ProtocolAdapter",
    "ProtocolRegistry",
    "TokenRecord",
    "TradeSide",
    "canonical_event_topic",
    "enrich_erc20_metadata",
    "materialize_dexscreener_snapshot",
    "materialize_trade_snapshot",
]

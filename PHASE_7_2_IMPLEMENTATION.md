# Phase 7.2: BSC Token / Pair / Trade Discovery

## Reuse of Phase 7.1

The existing block/log stream, WebSocket reconnect and HTTP polling fallback, raw EVM log storage (`raw_chain_events`), durable checkpoints, confirmation promotion, replay, and idempotent storage remain unchanged. Phase 7.2 consumes those rows through `BSCMarketPipeline`.

## Protocol adapters

`smms.market.protocols.ProtocolAdapter` defines `detect`, `decode_pair_created`, `decode_swap`, `decode_liquidity`, and `normalize`. `PancakeSwapAdapter` is the first implementation; `ProtocolRegistry` is the extension point for Flap and future launchpads/DEXs. Protocol selectors and ABI layouts stay outside ingestion.

## Event schemas and discovery

`Token` now records BSC creation transaction, block, and timestamp. `Pair` stores pair address, token ordering, factory, DEX, quote asset, and creation evidence. `MarketEvent` is the unified domain record with event type, token/pair/wallet, transaction identity, amounts, USD/price fields, side, source, and confidence. Identity is always `(chain_id, contract/address)` and `(chain_id, tx_hash, event_index)`; symbols are never identifiers.

Pair-created events discover both token contracts and quote assets (WBNB, USDT, USDC, BUSD plus configured assets). Swap decoding preserves raw amounts and emits BUY/SELL only when the observed in/out legs support it; otherwise side is `UNKNOWN`. Liquidity mint/burn events become `LIQUIDITY_ADD`/`LIQUIDITY_REMOVE`. Malformed or unsupported logs are retained as `UNKNOWN` with a reason and original topic metadata.

## Radar source priority

`GET /api/bsc/market-events` reports chain discovery as the authoritative event source and DexScreener as enrichment (`source_priority=[chain_discovery,dexscreener_enrichment]`). Conflicts are represented by source metadata and are not silently overwritten. The existing `/api/bsc/tokens` DexScreener feed remains available.

## Known limitations

Pair logs alone do not expose ERC-20 symbol/decimals or creator metadata; those require read-only contract calls/transaction enrichment in a later increment. Swap token orientation requires the pair record; events without a discovered pair retain both legs and may remain `UNKNOWN`. USD values require a price oracle or DexScreener enrichment. Flap selectors remain configuration/verification gated. Smart Money Score is intentionally out of scope.

# Phase 7.3: Early Buyer Engine

## Delivered

- `EarlyBuyerConfig` centralizes defaults (`top_n=20`, 24-hour safety horizon) and supports `buyers`, `blocks`, and `minutes` windows.
- `identify_early_buyers` consumes normalized BUY/SELL events, deduplicates `(tx_hash,event_index)`, ranks deterministic first buys, and emits entry delay buckets.
- `calculate_position` uses Decimal FIFO cost basis for cumulative bought/sold, balance, invested/returned capital, realized/unrealized PnL and ROI. Missing price, USD, or market-cap values remain unknown.
- Added durable `early_buyers` and `wallet_token_positions` tables plus idempotent repositories and replay facade (`EarlyBuyerEngine`).
- Added `GET /api/radar/tokens/{token}/early-buyers` with `limit`, `sort`, and `time_window` (`buyers:N`, `blocks:N`, `minutes:N`). TradeEvent is authoritative; MarketEvent is a compatibility fallback.

## Accuracy limits

Market cap is only reported when present in normalized source data; no supply-based estimate is fabricated. Missing USD/quantity prevents exact PnL and returns `null`. Reorg/orphan rows are excluded and correction evidence is retained in position metadata. Results are recomputed from the full observed stream, preserving losers, zero-balance wallets, and partial exits (no survivorship filtering).

## Next phase

Add authoritative token decimal/supply enrichment and a background replay job that materializes snapshots from confirmed chain events. Smart-money scoring remains intentionally out of scope.

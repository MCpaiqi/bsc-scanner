# Phase 4 Implementation

## Delivered

- Added chain-neutral BSC and launchpad seams in earlier Phase 4 steps.
- Added a configuration-driven Flap adapter under `src/smms/launchpads/flap/`.
- Added historical Flap token discovery from configured factory creation logs.
- Added canonical Flap `TradeEvent` decoding from transaction receipts and logs.
- Added configured lifecycle decoding for trading, near-graduation, graduation, and DEX listing events.
- Added an injected bonding-curve reader with explicit `DataUnavailable` failures when verified chain facts are absent.
- Added Flap configuration placeholders to `configs/default.yaml` and `configs/sources.yaml`.

## Verification Boundary

The repository does not contain verified Flap contract addresses, event signatures, ABI field positions, curve parameters, fees, graduation thresholds, or token decimals. These remain unset and must be supplied only after on-chain or official-source verification. The adapter does not fabricate them. The checked-in Flap YAML is disabled until those values are supplied.

Trade direction also requires an explicit buy/sell topic, side field, or configured default. A generic event topic alone is rejected because it cannot establish BUY versus SELL safely.

## Not Implemented Yet

- Live Flap WebSocket ingestion.
- Verified Flap ABI bindings and bonding-curve math.
- Flap liquidity, tax, holder, and contract-risk analysis.
- CLI commands for Flap discovery/scan and database persistence orchestration.
- Live trading; `ENABLE_LIVE_TRADING` remains disabled by default.

## Tests

The Flap unit tests cover configured creation-log discovery, BUY decoding, graduation lifecycle mapping, and the verified bonding-curve reader seam. The full suite passes with existing Solana behavior preserved.

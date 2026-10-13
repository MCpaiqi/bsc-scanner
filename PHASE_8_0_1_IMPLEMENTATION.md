# Phase 8.0.1 - BSC Wallet Intelligence Production Hardening

## Scope

This phase hardens the existing Phase 8 wallet intelligence path. It does not add a trading strategy, enable live execution, or introduce a new chain. The primary product path remains BSC while existing Solana adapters and explicit chain=solana compatibility remain available.

## Problems Fixed

- Centralized BSC-first chain and EVM address normalization with explicit invalid-chain validation.
- Point-in-time wallet snapshot selection: one latest snapshot per (chain, wallet, score_version) at or before as_of.
- Future-event filtering for profiles, flow, graph, replay, and snapshot reads.
- Explicit UNKNOWN risk/evidence handling; missing provider data does not become safe.
- Wallet network influence now counts wallet-network edges only, not wallet-token activity.
- Relationship aggregation preserves bounded evidence count, token count, first/last observed times, and samples.
- Graph construction is bounded by node, edge, event, wallet-per-token, relationship, and pair-work limits.
- Repeated co-buy aggregation uses an inverted token index instead of an unconditional wallet-pair scan.
- Deterministic hashed cluster identifiers include chain, canonical members, and algorithm version.
- Wallet replay and flow reads are filtered and bounded in SQL with explicit truncation metadata.
- BSC RPC and WebSocket endpoint lists support deterministic multi-provider rotation, chain-id checks, and capability probes.
- WebSocket failure transitions to HTTP polling and can fail back only after hysteresis and catch-up; shared log identity deduplication protects the boundary.
- Raw events and trade projections retain idempotent (chain/chain_id, transaction hash, log index) identity.
- DexScreener timeout, rate limiting, TTL cache, stale evidence, and public-API key reservation are wired through settings.
- GoPlus uses app-key/app-secret token acquisition and Authorization: Bearer; legacy query-string bearer behavior is removed.
- Status and error details redact URLs, tokens, secrets, and authorization data.
- Flap remains disabled and VERIFY_ON_CHAIN; readiness reports NOT_CONFIGURED or FLAP_NOT_VERIFIED without fabricated evidence.
- Wallet Intelligence UI defaults to BSC and reports graph truncation.

## Architecture

    BSC WebSocket endpoints
            |
            v
    WS retry / alternate endpoint rotation
            |
            v
    HTTP eth_getLogs polling fallback
            |
            v
    checkpoint + confirmation depth + canonical log identity
            |
            v
    raw chain events -> normalized trades -> wallet intelligence -> Radar
            ^
            |
    periodic WS health probe -> bounded catch-up -> hysteretic failback

HTTP fallback providers are accepted only after eth_chainId, eth_blockNumber, and a minimal eth_getLogs capability check. Endpoint URLs are never returned verbatim by diagnostics.

## Data Integrity

- All point-in-time intelligence applies timestamp/as_of <= cutoff.
- Wallet snapshots use the latest row per wallet at the cutoff before applying the smart-money threshold.
- BSC addresses are lower-case internal identities; Solana strings remain unchanged.
- Raw event and normalized trade writes are idempotent across WS replay, HTTP boundary replay, restart, and failback.
- Removed logs remain orphaned and do not continue to project into canonical market state.
- Confirmation depth controls pending-to-confirmed promotion.
- Unknown or unavailable external evidence remains visibly unknown with reduced confidence.

## Configuration

The supported provider configuration is documented in .env.example:

- BSC_RPC_URLS / BSC_RPC_URL and BSC_WS_URLS / BSC_WS_URL (plural values take precedence, preserving order and removing empty duplicates).
- BSC_CHAIN_ID=56, RPC/WS retry and timeout settings, confirmation depth, polling interval, and WS failback probe hysteresis.
- DEXSCREENER_* timeout, rate, cache TTL, and public API reservation settings.
- GOPLUS_ENABLED, GOPLUS_APP_KEY, and GOPLUS_APP_SECRET; legacy GOPLUS_API_KEY is retained only for compatibility and is not treated as a bearer token.
- ENABLE_LIVE_TRADING=false and TRADING_MODE=PAPER remain the safe defaults.

## Database Changes

Migration 20260913_0020_phase8_hardening_indexes adds bounded lookup indexes for trade_events(chain, trader_address, timestamp), trade_events(chain, token_address, timestamp), and wallet_performance(chain, wallet_address, as_of). No existing table or field was removed.

## Tests and Verification

The focused Phase 8.0.1 regression file is tests/unit/test_phase8_0_1_hardening.py. It covers default/explicit/invalid chains, cutoff filtering, graph truncation and aggregation, invalid Radar chain input, and GoPlus Bearer authentication. Existing provider, graph, stream, risk, snapshot, and external-evidence suites remain in the source repository.

Commands used for release verification:

    pytest -q                         # 225 passed, 11 warnings
    ruff check src tests               # passed
    npm run build (from web/)          # passed (tsc -b + vite build)
    npm test -- --run (from web/)     # 3 files, 5 tests passed

`ruff format --check src tests` was also run and reports the repository baseline:
136 files would be reformatted and 70 are already formatted. The phase changes
were not used as a reason to reformat unrelated historical files.

The health regression assertion is environment-safe: it expects ok when the configured BSC endpoint list is present and not_configured otherwise. This avoids coupling the suite to a developer-local .env file while preserving the configured-versus-unconfigured production behavior.

## Known Limitations

- Flap has not been verified from a complete real creation/trade evidence set and remains VERIFY_ON_CHAIN/disabled.
- No live RPC or WebSocket verification was required for unit completion; credentials are environment-dependent and no transaction path is enabled.
- Repository-wide Ruff formatting is not clean because of 136 pre-existing unformatted files; lint (`ruff check`) remains clean.
- The repository contains prior release/test artifacts and unrelated dirty-worktree changes; they were preserved.

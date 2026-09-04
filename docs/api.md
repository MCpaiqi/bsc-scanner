# API

Phase 0 exposes `GET /health`. Token, wallet, signal, paper, and backtest routes will be added as their respective phases land.

## Provider interface

`DataProvider` exposes `get_token`, `get_transactions`, and `get_swaps`. `MockProvider` is deterministic and intended for tests and offline development. `SolanaRPCProvider` requires `SOLANA_RPC_URL` and returns `DataUnavailable` when the source is not configured, unreachable, or missing authoritative data.

Raw provider responses are persisted in `raw_chain_records` using `(provider, record_type, external_id)` as the idempotency key. Standardized records are generated only from required fields; unavailable timestamps, signatures, and addresses are rejected with `NormalizationError`.

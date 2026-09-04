# Data Model

Phase 1 persists the research data needed by later ingestion, scoring, signal, backtest, and paper-trading phases. All timestamps use timezone-aware UTC values. Monetary values are `NUMERIC`, never floating point; chain amounts retain raw integer-compatible and normalized columns.

| Table | Responsibility | Idempotency / key |
| --- | --- | --- |
| `tokens` | Solana token profile and market snapshots | `(chain, address)` |
| `wallets` | Wallet profile and aggregate metrics | `(chain, address)` |
| `wallet_transactions` | Normalized wallet transaction / swap records | `(wallet_id, signature)` |
| `token_wallet_events` | Token-scoped buy, sell, transfer, and LP events | Indexed by token/wallet and time |
| `wallet_scores` | Immutable wallet-score history | `(wallet_id, timestamp, version)` |
| `token_scores` | Immutable token-score history | `(token_id, timestamp, version)` |
| `signals` | Explainable research signals | Indexed by token and creation time |
| `paper_trades` | Future simulated trade ledger | Links token and optional signal |
| `raw_chain_records` | Source payload preservation | `(provider, record_type, external_id)` |
| `ingestion_runs` | Ingestion observability | Run identifier |

`raw_chain_records` retains the source payload independently from normalized transaction/event rows. Future normalization must reference source identifiers in metadata and must never overwrite source payloads.

## Migrations

Apply the initial schema with `python scripts/init_db.py`, which runs Alembic revision `20260904_0001`. Production schema changes must add a new Alembic revision; do not call `Base.metadata.create_all` against a production database.

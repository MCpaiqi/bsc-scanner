# Phase 7.5.1 Implementation

## Scope

Hardening and data correctness for the Phase 7.1-7.5 BSC intelligence chain. This phase stops at Smart Money intelligence and does not add the Phase 7.6 Risk Engine.

## Problems Fixed

- `smms.ingestion` no longer imports the BSC stream or persistence-backed ingestion service during a base package import. `DataProvider`, `DataUnavailable`, raw types, and `MockProvider` work without Redis, PostgreSQL, WebSocket, or a BSC client.
- ROI and median ROI use the bounded, monotonic transform `max(roi, 0) / (max(roi, 0) + scale)`. The default scale is `0.25`; scales are configurable in `configs/scoring.yaml`. A 100x or 1000x outcome approaches, but never reaches, the full component weight.
- Same-block activity remains a correlation indicator. It is no longer included in the Bot probability heuristic and cannot independently increase wash-trading probability.
- Token aggregation only counts smart wallets that actually traded the target token. Cluster deduplication keeps the highest-scoring participant.
- API radar paths no longer replay the wallet universe at request time. They read materialized snapshots and return `UNKNOWN` when a refresh has not produced data.

## Snapshot Model

`WalletSmartMoneySnapshot` stores wallet, score, confidence, `score_version`, `profile_version`, `as_of`, sample size, completeness, historical coverage, trade quality, profile JSON, normalized components, contributions, and `updated_at`.

`TokenSmartMoneySnapshot` stores token, smart-wallet count, buy/sell/net USD flow, early smart-buy count, score, confidence, data status, `as_of`, `score_version`, top wallets, and `updated_at`.

The uniqueness keys include the cutoff and versions, so multiple score versions coexist. Re-running the same refresh is an upsert and does not create duplicate rows.

## Aggregation Boundary

`IntelligenceAggregationService.refresh(as_of=...)` is the single materialization entry point:

`TradeEvent <= as_of -> WalletProfileReplay -> Smart Money score snapshot -> Token Smart Money snapshot`

Manual invocation:

```text
smms intelligence refresh --chain bsc --as-of 2026-09-11T10:00:00+00:00 --score-version v2 --profile-version v1
```

The CLI commits one transaction after the synchronous aggregation implementation runs through the async session adapter.

## Point-in-Time Guarantees

- Trade reads use `timestamp <= as_of`.
- Persisted profile/performance inputs are accepted only when their final observation is `<= as_of`.
- Snapshot reads select the newest row with `snapshot.as_of <= requested as_of` and the requested score version.
- Future trade, future wallet performance, and future score rows therefore cannot contaminate historical results.

## Migration and Indexes

Migration `20260911_0010_intelligence_snapshots` follows `20260911_0009` and creates both snapshot tables. It adds only query-backed indexes: canonical event chain/block lookup, token/pair event time lookup, snapshot wallet/token cutoff lookup, and snapshot ranking indexes.

Alembic has one head: `20260911_0010`.

## Verification

- `pytest -q --basetemp .phase751-final-tmp`: **111 passed** (workspace temp path used because the Windows default pytest temp root denies access)
- `ruff check src tests`: **passed**
- `python -m compileall -q src`: **passed**
- `node --check src/smms/api/static/app.js`: **passed**
- `git diff --check`: **passed**

## Remaining Limitations

- Aggregation is manual/CLI driven; no queue or scheduler is introduced in this phase.
- Snapshot refresh currently replays the bounded canonical event set once per invocation. Incremental checkpoints and partitioning remain future performance work.
- Same-block correlation does not claim bot/wash detection. Funding relationships, same-direction/amount similarity, temporal proximity, and repeated synchronization are intentionally deferred.
- API callers must refresh snapshots before expecting non-`UNKNOWN` radar intelligence.

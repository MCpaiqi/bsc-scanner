# Alpha Coverage Integrity Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans, test-first fixes and
> one fresh read-only review; execute continuously under the active user goal.

**Goal:** R4/R8: restore trustworthy live Alpha labels, persistent recovery and a
score range consistent with its high-score threshold. The full programme remains
`docs/RESEARCH_RELIABILITY.md`; R3 transitive reconstruction and R6–R14 stay open.

**Architecture:** Persist confirmed, per-pair bounded HTTP scan receipts alongside
canonical raw/market/trade facts. A dedicated coverage worker automatically fills
the first 65 minutes of a canonical pair. Alpha snapshots double as durable jobs:
inputs await verified entry coverage, outcomes await verified horizon coverage;
neither is discarded by a sliding age window. New pairs enqueue work atomically;
a bounded cyclic repair cursor covers legacy/imported rows and ID/commit inversion.

## Constraints and policy

- BSC only, PAPER/SHADOW, UTC, explicit UNKNOWN and immutable scored inputs.
- Receipt proves exact pair, Swap topic, chain, confirmed block range, start/end
  headers and parent linkage; zero-log successful scans are valid evidence.
- RPC/decode/persistence/hash failure cannot create a valid receipt. Query receipts
  and canonical facts commit together; lease/generation checks reject stale work.
- A negative label requires verified entry AND full future horizon coverage.
  An observed positive may be shown once entry/window evidence qualifies, but
  rate denominators use only fully covered mature 1m token/pair observations.
- Coverage worker runs independently of Forward scoring, with bounded work,
  durable retries and no Redis dependency. Tests use fake RPC, not real providers.
- Snapshot task state and next attempt survive downtime. A reorg invalidates
  receipts and old-generation research inputs; automatic reconstruction of already
  scored historical inputs remains the explicit R3 gate, never silently trusted.
- New recipe `experimental-v3`: rank observed momentum/participation/concentration
  on a documented 0–100 scale; missing risk/liquidity remains UNKNOWN. Preserve
  v1/v2 history. Labels carry a version and frozen notional policy fingerprint.

## Tasks

- [x] Reproduce missing-coverage negatives, 75m task loss and unreachable high
  scores through existing public functions. Keep the failing test evidence.
- [x] Add migration after 0035, persistent pair coverage jobs/receipts/cursor,
  nullable pending Alpha score and task/retry/generation fields. Legacy v1/v2
  rows retain their data and version. Add real PostgreSQL migration tests.
- [x] Implement coverage proof assembly, strict header/adjacency checks and
  canonical generation filtering. Prove gaps, zero-log scans, wrong pair/topic,
  failed queries and rollback cannot establish coverage.
- [x] Implement leased bounded confirmed-RPC collector, atomic raw→canonical
  persistence + receipts, and explicit errors/retries. Integrate pair creation,
  a dedicated runtime/Compose worker, heartbeat and reorg invalidation.
- [x] Replace sliding-window Alpha work with durable pending jobs and bounded
  cyclic repair. Score only verified entry inputs, pin units/notional/versions,
  keep negatives UNKNOWN until full coverage and recover after long downtime.
- [x] Expose current cohort/coverage/policy in API and correctly separate known
  rates from missing evidence. Verify current/legacy coexistence and 0–100 score.
- [x] Fresh reviewer, one regression-driven material fix pass; full backend,
  PostgreSQL collector/proof/migration tests, web tests/typecheck/build, Ruff.
- [x] Document actual acceptance and remaining programme work, clean only owned
  verification resources and commit only this batch's files; no deploy/merge/push.

## Review focus

Confirmed ranges with no logs; block or time gaps; duplicate boundaries; mismatched
fork/parent hashes; stale leases; removed pairs; pending older than 75m; independent
DB commit order; zero/dust/unknown quote amounts; bounded cohort denominators;
current/legacy version isolation; result provenance and transactional rollback.

## Ledger

Previous goal turn was progress: verified commit `0740694` and isolated PostgreSQL
tests. Current tracked worktree is clean at that commit; existing untracked product
source/data are preserved. Ruling: raw event/checkpoint existence is not a coverage
proof. Use explicit confirmed HTTP receipts and automatic pair backfill; never make
missing data a negative merely because the wall clock passed the horizon.


## Execution and review, 2026-11-06

- Observed RED through existing live functions for premature negatives, lost
  pending tasks, unreachable high-score band and zero-quote division. The durable
  scheduler and experimental-v3 policy passed those cases.
- Confirmed automatic HTTP receipts, fixed-scope proof assembly, transactional
  raw/market/trade projection, expiring leases, frozen policy IDs and fixed 1m
  cohorts. Scored input reconstruction remains R3; missing entry stays pending.
- One fresh reviewer identified three material issues. Reproduced all before
  fixes: first/empty in-flight scans missed reorg invalidation, production RPC
  null/nonobject results silently became empty, and canonical rediscovery left
  unscored jobs INVALIDATED. Added a canonical publication fence/block anchors,
  strict RPC array validation and unscored job resumption in one fix pass.
- Two further boundary regressions failed before fixes: entry Swap in creation
  block was excluded by the shared exclusive VWAP start; a timestamp collision
  could rejoin an invalidated snapshot into the current candidate list. Both are
  covered without altering historical recipe semantics.
- Ruling: keep one database publication fence across scans/reconciliation, with
  RPC outside the transaction and consistent fence→job lock order. Only canonical
  reconciliation inserts block anchors, so stale collectors cannot poison old
  ranges. Anchor retention and dense transaction latency stay explicit R11 work.
- Ruling: preserve invalidated scored inputs and require explicit R3 reconstruction;
  only unscored snapshots may be rescheduled after rediscovery. Never silently
  overwrite scored history. No deployment, merge, push or real RPC was performed.
- Isolated PostgreSQL 16 verification passed **39 cases**, including raw/market/
  trade rollback, two-connection publication/reconciliation, out-of-order ID
  commits, migration 0035→0036 with real legacy values and refused lossy downgrade.
- Web **41 passed**, TypeScript/build passed; Compose config and Alembic head
  `20261106_0036` validated. Final backend and cleanup evidence is recorded in
  `docs/RESEARCH_RELIABILITY.md` before the local batch commit.

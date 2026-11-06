# BSC research desk reliability programme

Objective: 让它成为一个能长期运行、数据可追溯、研究结果可信的 BSC 链上研究台。

This is the persistent scope and evidence ledger for the active goal. Completing
one batch does not complete the programme. Keep BSC production scope, PAPER /
SHADOW operation, UTC timestamps, on-chain discovery, provider provenance and
explicit UNKNOWN states. Preserve existing local data and untracked source.

## Acceptance requirements

- [x] R1: background reorg reconciliation and ingestion make progress concurrently;
  cancellation releases the database lock and canonical reorg invalidations persist.
- [x] R2: historical builder → serialization → materialization → backtest preserves
  risk meaning, including CRITICAL, UNKNOWN and legacy dataset payloads.
- [ ] R3: removed/noncanonical chain facts cannot enter rebuilt historical universe,
  features, outcomes or newly aggregated risk; genuine losers remain in the universe.
- [x] R4: Alpha negatives require complete horizon coverage; missing coverage stays
  UNKNOWN; pending work survives downtime; result versions and denominators are explicit.
- [x] R5: forward endpoints respect a bounded time tolerance and canonical facts;
  drawdown uses price / running peak - 1; historical and live evaluation agree.
- [ ] R6: normal and deferred candidate projections have equivalent audit/baseline/
  rule effects; event fanout happens after commit and survives retry without ghosts.
- [ ] R7: SSE reconnect/replay has explicit bounded recovery semantics under bursts.
- [x] R8: Alpha score range, available evidence, threshold and UI classifications agree.
- [ ] R9: complete candidate sorting/filtering/pagination, continuously refreshed
  detail/statistics and separate unavailable/error/stale states are demonstrated.
- [ ] R10: annotations and token/global alerts have explicit scope and visible save results.
- [ ] R11: evaluation work is bounded by due pending tasks; prices are fetched in
  batches; VWAP processing avoids quadratic rescans; runtime latency is observable.
- [ ] R12: clean-checkout backend/frontend checks and PostgreSQL migrations run in
  CI; release dependencies/configuration are reproducible; critical paths have
  restart/reorg/rollback/coverage interruption tests.
- [ ] R13: deployment restricts database/Redis exposure and protects analyst writes;
  documentation matches actual services; credentials are never published.
- [ ] R14: the full Compose topology passes an isolated persistence/restart and
  end-to-end smoke test; remaining live-provider limitations are explicit.

## Delivery sequence

1. Foundation integrity: R1–R3, including fresh regression evidence and review.
2. Evaluation integrity: R4, R5, R8, with versioned reconstruction guidance.
3. Runtime closure: R6, R7 and restart/recovery behavior.
4. Research workflow: R9, R10 and user-visible freshness.
5. Bounded operation and reproducible delivery: R11–R14, then completion audit.

## Initial evidence, 2026-11-05

Prior read-only audit: Python 417 passed, 2 optional PostgreSQL tests skipped;
web 40 passed, typecheck/build and scoped Ruff passed. Those results do not prove
the above acceptance requirements. Fresh checks are required for each delivery.

Current checkout initially has no tracked modifications and 53 untracked status
entries, including working frontend source. Do not clean, overwrite or silently
commit local data, archives or stage reports.

Ruling: work on `codex/research-integrity` in this checkout, preserving the current
untracked frontend — a HEAD-only worktree would omit the actual product under
review. No deployment, production data rewrite, merge or push is part of a local
fix. Record verified changes and remaining requirements below.

## Progress

- Foundation batch started; current source revalidated against the audit.
- Reconciler regression failed with a 2s timeout, then 36 stream/reorg tests passed
  after removing nested lock acquisition. Risk contract regressions failed in five
  paths, then 51 dataset/signal tests passed after fixing producer and legacy reader.
- Fifteen independent canonical/removed/JSON-marker regressions failed before the
  canonical fact predicate module was added. The corrected reads preserve a genuine
  losing token and valid unmarked historical facts.
- Ruling: bump the default extraction identity from dataset-v2 to dataset-v3 —
  canonical fact selection and risk exports change research inputs. Existing
  materialized datasets remain immutable local artifacts and must be rebuilt under
  a new dataset_version before trusting corrected canonical coverage. Legacy risk
  `level` reads remain supported; this does not sanitize old stored orphan facts.
- Fresh review found three further integration gaps: whole live transaction
  ownership, cancellation during DB I/O, and cached launchpad admission after an
  orphan CREATED. Added deterministic run interleaving, real handoff persistence,
  orphan CREATED and future-fact isolation regressions and fixed all three.
- Verification after the review fix pass: full backend **452 passed, 2 skipped,
  13 existing deprecation/reflection warnings**; web **40 passed**, typecheck and
  build passed; `ruff check src tests scripts --no-cache` and diff whitespace
  check passed. Initial full run failed the factory handoff; it is green after
  correcting DB ownership and safe shutdown.
- PostgreSQL 16 isolated verification: all 34 Alembic revisions upgraded a fresh
  database to `20260919_0034 (head)`; the research regression command passed all
  **35 cases**, including **32 sync research cases on actual PostgreSQL** and
  **3 async ingestion cases on SQLite**. No production database was accessed.
- R3 is locally corrected for direct facts and newly aggregated risk. It remains
  open for full provenance/dependency invalidation and reconstruction of existing
  derived snapshots; old materialized research results are not retroactively clean.
- Review minor deferred: materialized LOW/UNKNOWN and a competing future risk
  snapshot are not separate full materialization cases yet (builder→JSON tests
  cover LOW/CRITICAL/UNKNOWN; materialization specifically covers CRITICAL).
- Review scope rulings: PostgreSQL execution was completed locally after review;
  full-topology/live-provider verification remains R14. R4–R14, rewriting old
  results, and baseline strategies intentionally ignoring risk are not asserted
  complete by this batch. No provider calls or baseline policy changes were made.

### Forward evaluation batch, 2026-11-05

- Previous goal turn was verified progress, commit `2093841`. This batch implements
  R5 with `price-path-v2` shared by historical/live evaluation and `forward-v2`
  persisted outcomes. It does not close R4 Alpha coverage or R3 dependency rebuild.
- Added explicit USD entry/future source checks, canonical token/pair/time filtering,
  nearest ±5s endpoints, true peak drawdown including entry, recipe/source/path
  provenance, and performance as-of filtering. Unknown entry units remain UNKNOWN;
  no legacy units or numbers are silently rewritten.
- Migration `20261105_0035` preserves existing outcomes as `legacy-v1`, permits
  current results alongside them, records retries and endpoint metadata, and
  refuses a lossy downgrade. Return NUMERIC storage no longer has the six-integer-
  digit cap. Historical backtest summaries identify their evaluation recipe.
- Ruling: execute the localized Forward work before durable Alpha coverage, while
  keeping the entire original objective. Ruling: require explicit USD evidence
  rather than guessing a basis for old generic prices. Ruling: atomically enqueue
  production observations' tasks and cyclically repair bounded historical slices;
  never assume database IDs follow commit order. Ruling: widen numeric storage and
  retain calculation strings rather than clipping legitimate finite returns.
- Fresh independent review found an ID/commit-order loss and a NUMERIC overflow
  that could roll back neighboring work. Both were reproduced against real
  PostgreSQL before correction; new regressions now prove their fixes. The reviewer
  reported no additional actionable minors. R3 transitive invalidation, R4 complete
  coverage and deployment/providers/throughput were explicitly outside this review
  and remain open programme requirements.
- Final verification: full backend **487 passed, 4 skipped, 13 existing warnings**;
  web **40 passed**, typecheck/build passed; Ruff and diff whitespace checks passed.
  Two added skips require explicit isolated PostgreSQL (migration test and genuine
  two-writer visibility). The two pre-existing optional DB skips remain.
- Isolated PostgreSQL run: **37 passed**, comprising 30 database Forward cases,
  6 pure calculation cases and 1 real historical migration test. Tests include
  legacy 0034→0035 data preservation/current coexistence, refused lossy downgrade,
  late lower-ID commit visibility, persistence of a `999999999999` return next to
  normal work, and retry/cursor behavior after creating a new Session.
- R11 is only partially advanced: due/repair task counts are bounded and prices
  are fetched by merged token/time ranges. Very dense path streaming/caps, Alpha
  quadratic VWAP work and global runtime acceptance remain pending.
- Policy/reproduction details: [FORWARD_EVALUATION.md](FORWARD_EVALUATION.md).

## Reproducing the foundation regressions

Default offline checks:

```powershell
python -m pytest tests/unit/test_research_integrity.py -q
```

To run the sync research cases on a dedicated PostgreSQL database, explicitly set
`SMMS_RESEARCH_TEST_DATABASE_URL` to its SQLAlchemy psycopg2 URL and run the same
command. Each sync case creates and removes its own generated `smms_research_*`
schema; the fixture does not load a URL from `.env`. Ingestion cases deliberately
use their own in-memory SQLite fixtures. Full migration/runtime acceptance is a
separate gate; schema creation inside regressions does not prove migration parity.


### Alpha coverage batch, 2026-11-06

- R4 and R8 are verified locally. Live `experimental-v3` snapshots are durable
  pending tasks with nullable scores, frozen quote notional/policy fingerprints
  and `alpha-shadow-v2` labels. Missing entry or future coverage stays UNKNOWN;
  pending tasks recover after two days. Legacy scores/labels remain unchanged.
- A dedicated `alpha-coverage` worker performs bounded confirmed per-pair HTTP
  scans, validates logs/callers/hashes and atomically persists raw/market/trade
  facts plus receipts. Empty success is evidence; null/malformed RPC data is an
  error. Leases, retry times and safe error codes survive restart.
- Proof requires canonical generation, creation anchor and block/parent continuity.
  Reconciliation/publication share a database fence, including before the first
  empty receipt exists. Canonical rediscovery resumes unscored work; scored
  invalidated history is retained for R3 reconstruction.
- Rates aggregate one mature 1m token/pair observation with verified full-hour
  coverage and matching execution policy. Four observation windows cannot
  multiply the denominator. Observed positives with partial coverage are excluded.
- The versioned score normalizes actual available activity components to 0–100;
  the >=70 band is reachable, missing buyer concentration supplies zero, and
  risk remains UNKNOWN. UI exposes verified versus pending coverage.
- Fresh read-only review found three material gaps (first-scan fork race,
  tolerant malformed RPC arrays, canonical rediscovery). All reproduced RED and
  passed after one fix pass. Creation-block VWAP inclusion and invalidated
  timestamp rejoin also received failing-then-passing boundary regressions.
- Final backend **523 passed, 7 skipped, 13 existing warnings**; optional skip
  paths include explicit isolated PostgreSQL cases. Actual PostgreSQL 16 command
  passed **39 cases**, including two independent publication/reconciliation
  connections, commit-order inversion, transactional rollback and historical
  0035→0036 migration with legacy data and a refused lossy downgrade.
- Web **41 passed**, TypeScript/build passed; full Ruff, whitespace, Compose config
  and migration head `20261106_0036` passed. Isolated schema count was zero;
  the owned PostgreSQL verification container and its anonymous volume were
  stopped/removed after ownership-label validation. No production DB/provider
  was accessed and no deploy, merge or push occurred.
- R3 remains open for automatic versioned reconstruction of already-scored inputs.
  R6/R7 projection/outbox/replay, R9/R10 workflow, R11 dense-path/VWAP/anchor
  retention/publication latency, R12 reproducibility/CI, R13 deployment access
  and R14 full-topology restart/live limitations remain required goal work.
- Details: [ALPHA_COVERAGE.md](ALPHA_COVERAGE.md). Apply migration 0036 before
  the updated API/ingestion/Forward/coverage workers are started.


### Alpha reconstruction slice, 2026-11-06

- Advanced R3 without closing it: Alpha now appends input revisions linked to
  predecessors. Old score/features/entry inputs and invalidated outcomes remain
  auditable; new pending work waits for its canonical coverage generation.
- Exact input manifests identify pair creation, trades/forks/amounts/units, receipt
  bounds, policies and semantic versions. Future evaluation has a separate manifest
  linked to the entry digest. 38-digit source values are preserved without Decimal
  context rounding; stable digests reproduce after database reload.
- Current/history API payloads contain source summaries, and a dedicated lineage
  endpoint exposes full facts on demand. Cursor history includes invalidated/legacy
  audit rows; current stats continue excluding them. Detail can show a NULL-score
  replacement as rebuilding. The drawer shows the revision and source link.
- Real-time removed evidence, including no local projection, fences publication and
  invalidates proof/research transactionally. Never-scanned PairCreated orphans,
  replacement creation before old removed notification, duplicate notices and later
  creation-block enrichment have explicit recovery regressions.
- Migration 0037 preserves old values, invalidates pre-manifest v3 scores and old
  receipt generations, and enqueues fresh confirmed rescans at runtime. The old
  removed-log path could leave stale receipts canonical; reusing their status
  without rescanning was rejected. No migration provider calls are made.
- One fresh reviewer identified two P1 lifecycle omissions; both were reproduced
  RED and fixed in one pass. Full backend **538 passed, 9 skipped, 13 existing
  warnings**; isolated PostgreSQL **56 passed**; web **42 passed**, TypeScript/build,
  full Ruff, whitespace, Compose config and Alembic 0037 head passed. Owned test
  schemas/container/anonymous volume were cleaned after ownership checks.
- R3 still requires market/risk/wallet-token intelligence, early-buyer/radar derived
  snapshots, Forward entry lineage and exported dataset/backtest staleness plus
  reconstruction. These are not proven by Alpha tests. R6–R14 remain open as above.
- Full provenance source loading/retention and canonical-publication lock latency
  remain R11 performance work. Clean-checkout/frontend source closure remains R12;
  only the necessary existing type file was deliberately captured this batch.
- Details: [ALPHA_RECONSTRUCTION.md](ALPHA_RECONSTRUCTION.md). No real RPC,
  production data rewrite, deploy, merge or push occurred.

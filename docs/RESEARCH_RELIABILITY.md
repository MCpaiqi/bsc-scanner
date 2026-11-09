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
- [x] R6: normal and deferred candidate projections have equivalent audit/baseline/
  rule effects; event fanout happens after commit and survives retry without ghosts.
- [x] R7: SSE reconnect/replay has explicit bounded recovery semantics under bursts.
- [x] R8: Alpha score range, available evidence, threshold and UI classifications agree.
- [x] R9: complete candidate sorting/filtering/pagination, continuously refreshed
  detail/statistics and separate unavailable/error/stale states are demonstrated.
- [x] R10: annotations and token/global alerts have explicit scope and visible save results.
- [ ] R11: evaluation work is bounded by due pending tasks; prices are fetched in
  batches; VWAP processing avoids quadratic rescans; runtime latency is observable.
  Canonical anchors/provenance and dense/UNKNOWN history remain retained; publication
  lock latency and asynchronous reconstruction throughput still require closure.
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


### Derived snapshot reconstruction slice, 2026-11-06

- Continued R3 with market, risk and wallet/token intelligence snapshots. Canonical
  invalidation advances a transactional BSC epoch, hiding transitive old derived
  values through centralized ORM criteria (including aliases/latest subqueries).
  Producers capture before source reads; stale publication cannot replace a newer
  winner. Direct risk identity-map access verifies persisted visibility in SQL.
- Publication appends immutable source/result revisions with predecessor, exact
  manifests/digests and dependency snapshot IDs. Per-identity locks converge
  concurrent writers. ORM mutations/reactivation are rejected. Numeric source
  serialization is stable across persisted int/Decimal reloads.
- Canonical parent-pair/time/block and JSON orphan checks protect child facts.
  Early entry cannot fall back to cached Token creation; previously observed empty
  subjects receive explicit UNKNOWN/sample-zero replacements. Market aggregation
  follows pair identity; partial USD is DEGRADED and no-input reconstruction is an
  UNAVAILABLE audit row, excluded from current markets.
- Unversioned Token risk cache is excluded; explicit resolved evidence and canonical
  trades provide inputs. Rebuilt missing external dimensions stay UNKNOWN while
  known behavior can still indicate risk. Missing legacy intelligence parameters
  use an explicit legacy-defaults-v1 manifest policy; unsupported recipes remain
  unavailable with a recorded job reason.
- Bounded persistent repair/jobs and an independent research-rebuild worker recover
  across restarts, retries, stale epochs and commit-order inversion. Public read-only
  research audit/epoch/job endpoints retain hidden values; normal DTOs add provenance
  URLs. Migration 0038 preserves old values, quarantines pre-lineage derived rows,
  preserves independent DexScreener observations and refuses destructive downgrade.
- One fresh review identified four verified races/lineage defects, all regression-
  fixed in one pass. Final backend **561 passed, 13 skipped, 13 existing warnings**;
  isolated database command **65 passed** (**61 actual PostgreSQL**, three existing
  async SQLite cases, one heartbeat simulation). Web **42 passed**, TypeScript/build,
  Ruff/whitespace/Compose/head and offline migration SQL generation passed.
- Owned test schemas were zero; labelled PostgreSQL container/anonymous volume and
  temporary SQL were removed. No real provider calls, production rewrite, deploy,
  merge or push occurred. Existing untracked source/data/release files were preserved.
- R3 remains OPEN: EarlyBuyer/positions, Radar/Evidence/Signal caches, Forward entry
  observations and materialized dataset/backtest staleness/reconstruction still need
  implementation and verification. R6–R14 are unchanged/open; coarse epoch invalidation,
  dense history/manifests and full health/dashboard/latency acceptance remain R11/R14.
- Details: [DERIVED_RESEARCH.md](DERIVED_RESEARCH.md). Updated services require
  migration `20261106_0038` before starting.

### Early-buyer and position reconstruction slice, 2026-11-06

- Continued R3 with canonical early-buyer replay for dataset-v4, Radar feature/
  signal membership and the public API. Old buyer caches cannot supply historical
  evidence. Canonical observed Pair creation, parent/flag/JSON validity, tx/log
  deduplication and original time cutoffs apply to both results and manifests.
- EarlyBuyer/WalletTokenPosition now use the shared immutable epoch/revision/
  predecessor/manifest/publication and durable rebuild contract. Removing the last
  input withdraws a wallet through an UNAVAILABLE audit version. Positions cover
  every observed trading wallet, including those outside the early-entry window.
- Migration 0039 preserves and quarantines old cache payloads; the explicit legacy
  reconstruction cutoff is first buy / last trade (1970 when unknown), with
  legacy-defaults-v1 parameters. Unknown quantities are nullable. Downgrade refuses
  revisions, unknown quantities or outstanding reconstruction work.
- Accounting excludes generic quote prices, honors explicit Trade.price_usd and
  uses captured event decimals to normalize raw Market amounts. Missing historical
  unit evidence stays UNKNOWN. Missing USD proceeds still consume inventory;
  missing quantities/costs cannot become zero positions or zero-cost profit. FIFO
  and first-N buyer ranking follow chain log order, with a deque for lot removal.
- One fresh read-only reviewer reproduced four material issues. All received RED
  regressions and were fixed in one pass: old identity-map values certified under
  a new epoch, raw/normalized amount mixing, ignored USD column and address-based
  buyer truncation. Source queries now refresh ORM values; concurrent epoch changes
  require a complete read retry. No second review cycle was requested.
- Ruling on review exclusions: cached Radar/Signal/Evidence, Forward entries and
  materialized dataset/backtest artifacts remain pending R3; full topology and
  real providers remain R14. The hot-path lifetime scan concern was addressed
  locally with bounded entry-only reads (512 + sentinel per source, first-day BUY
  evidence). Unsaturated truncated samples remain UNKNOWN; full-history streaming,
  retention, async reconstruction throughput and latency remain R11.
- Existing materialized artifacts are preserved; new extraction has dataset-v4
  identity. This slice does not certify or rebuild old exports. R3 and R6–R14
  remain OPEN under the original objective; no production rewrite/deploy/push.
- Verification after the fix pass: backend **589 passed, 16 skipped, 13 existing
  warnings**; isolated database command **61 passed**, comprising **59 actual
  PostgreSQL cases**, one async SQLite API case and one heartbeat simulation.
  Final downgrade-guard migration checks **3 passed** (one PostgreSQL migration,
  two SQLite migration paths). Web **42 passed**, typecheck/build passed; Ruff,
  whitespace, Compose configuration, Alembic 0039 head and full offline SQL
  generation (**1222 lines**) passed.
- Verified zero remaining owned schemas, then removed the purpose-labelled test
  PostgreSQL container/anonymous volume. Temporary SQL was removed. Existing
  untracked product/data/release files remain preserved and outside this commit.

### Additional acceptance evidence to retain

- Provider → risk recipe field/unit compatibility is still an R14 acceptance gate.
  During Radar/evidence work, a receipt-recovery test exposed that risk-v1 does
  not consume the provider's `is_honeypot` field. The receipt test initially
  assumed a CRITICAL policy that risk-v1 does not define; it now verifies source
  recovery/availability separately from scoring policy, also using supported
  `mintable` evidence. This is not a claim that risk-v1 handles honeypot flags.
  Preserve legacy semantics and introduce/verify the corrected risk recipe before
  declaring the overall research desk complete; validate provider aliases and units.
- The final R3 audit must also check invalid in-flight wallet computation lineage
  when a newer wallet publication wins (current trusted outputs are epoch-guarded),
  and remove historical TokenScore cache fallbacks while closing dataset rebuilds.
- R5 source parity is reopened while closing historical datasets: current
  HistoricalDatasetBuilder._event_payload still exports generic `price`, whereas
  the live Forward adapter uses explicit USD evidence. Shared price-path-v2
  arithmetic/live endpoint tests do not prove those extraction paths agree.
  Verify a distinct quote/USD counterexample and correct/version extraction before
  checking R5 complete again. Historical per-pair and currency consistency are
  required, not waived by the earlier calculation-level verification.

### Radar, Signal and dependency reconstruction slice, 2026-11-06

- Continued R3 with immutable RadarFeatureObservation and Signal revisions using
  source epochs, exact manifests, predecessor/audit access and durable jobs. Normal
  candidate views/filters, audit scheduling, production rules and new Forward
  observations check canonical projection pointers. Invalidated caches display
  REBUILDING/UNKNOWN rather than preserving old values under a new timestamp.
- Discovery predicates, parent/raw provenance, explicit USD prices, observation
  cutoffs and retained ORM values are rechecked. Recent sells refresh observation
  time without inventing discovery evidence. Signal parameters preserve complete
  weights/threshold/config, including Decimal formatting used by config hashes.
- Corrected same-epoch inputs now transactionally invalidate transitive dependents
  through a persisted reverse graph. Prerequisite publication holds shared row
  locks, closing the uncommitted-edge race without taking the canonical epoch lock.
  Bounded cyclic repair handles same-epoch INVALIDATED rows as well as older epochs;
  jobs wait on prerequisite recovery and never rewind a newer live candidate.
- 0040 preserves/quarantines legacy Radar feature/priority/status context and Signal
  values, backfills prior dependencies and quarantines their previously unchecked
  dependents once. Legacy-defaults-v1 reconstruction is explicit; lossy downgrade
  is refused. Rebuild status history is labelled at actual processing time while
  research as_of remains the original cutoff. Rollback restores invalidation too.
- Chain proof eligibility shares normalized evidence aliases with the resolver;
  current epoch, subject/proof/field/value and exact fact/raw fingerprints are
  required. Candidate evidence resolves stored observations, selecting the latest
  per provider/field. Unversioned Token evidence/launchpad cache cannot restore
  canonical labels. Live collection uses completion time; historical risk rebuild
  reuses eligible immutable receipts without network and excludes future receipts.
- Fresh review found seven material gaps: delayed baselines dropped, configured
  policy replaced by defaults, same-epoch dependencies left current, chain aliases
  bypassing validation, future provider prices admitted, old provider values chosen,
  and a two-statement epoch read race. All were reproduced and covered in the one
  fix pass. Sampled baselines now use a reconstructed discovery-time projection,
  T0-specific IDs/digest and UNKNOWN when no price existed. Shared wallet replay
  parameters exclude unrelated Token admission/window policies, fixing a regression
  where an old job could disturb a fresh custom policy through wallet revisions.
- Verification: backend **628 passed, 20 skipped, 13 existing warnings**; database
  command **104 passed**, comprising **102 actual PostgreSQL cases**, one async
  SQLite API case and one heartbeat simulation. Web **42 passed**, typecheck/build;
  Ruff/whitespace/Compose configuration/Alembic 0040 head and offline SQL generation
  (**1285 lines**) passed. PostgreSQL cases include source/dependent publication
  fencing, reorg progress, rollback, dependency backfill and restart reconstruction.
- Verified zero owned schemas, then removed the purpose-labelled PostgreSQL test
  container/anonymous volume and temporary SQL/log files. Existing untracked local
  product/data/release files are preserved; no real provider calls, production
  rewrite, deployment, merge or push occurred.
- Rulings on review exclusions: existing Forward entry/outcome reconstruction and
  materialized datasets/backtests remain R3; event projection/outbox and SSE remain
  R6/R7; pagination/workflow remain R9/R10; bounded dense history/retention/latency,
  clean-checkout delivery, access control and full Compose/provider acceptance remain
  R11–R14. The additional acceptance evidence above is retained. The full original
  objective and R3/R5/R6/R7/R9–R14 are still OPEN; this slice is not programme completion.

## Historical datasets and evaluation availability (2026-11-06)

- Continued R3 with BacktestDataset under the shared epoch/revision/immutable
  manifest/publication/dependency contract. Rows inherit current-parent visibility.
  Canonical invalidation and same-epoch source supersession withhold prior datasets;
  bounded durable repair waits for prerequisites and appends replacement study
  revisions. Stable labels resolve current data while old IDs remain audit-only.
- Dataset-v5 removes generic quote prices, mixed-pair outcomes and unversioned
  TokenScore features. It shares the Forward USD loader and 30-second entry
  selector, freezes the entry pair, includes the five-second 24h endpoint buffer,
  and preserves stable source references during historical path evaluation.
  Unproven launchpad cache labels cannot admit a token. Legitimate losers without
  usable USD entry prices remain represented as UNKNOWN.
- R5 is rechecked after extraction-to-evaluation public-path regressions prove
  quote/USD separation, frozen pairs, canonical input filtering, endpoint tolerance,
  true drawdown and equal-time source ties. Historical/live comparisons now freeze
  the same evaluation_as_of. Receipt observed_at/as_of and signal-time availability
  are checked separately; late receipts are accepted only at a sufficiently late
  evaluation cutoff. Reconstruction preserves the recorded cutoff.
- Migration0041 preserves/quarantines actual legacy metadata and payloads. Missing
  recipes explicitly use legacy-upgrade-to-dataset-v5 without claiming the unknown
  former extraction recovered. Unsupported recipes remain unavailable; new work
  prevents lossy downgrade. No real database/provider or production migration ran.
- Backtest API/CLI loaders reject stale datasets and recheck after calculation.
  API returns 409/REBUILDING plus audit URL; a bounded audit-only dataset rows
  endpoint retains old payloads. Trusted reads refresh persisted values and copy
  JSON. Source identity accompanies current CLI JSON/CSV exports; imported files
  are explicitly UNVERIFIED with content digest rather than certified current.
- One fresh read-only reviewer independently reproduced two material defects:
  late-receipt cutoff mismatch and mutable nested JSON cache reads. Both received
  regression coverage and fixes in one material fix pass. No second review or
  implementer agents were used. Known parameters, transaction rollback, concurrent
  publication convergence and old/new value preservation are exercised.
- Verification at final implementation: backend **650 passed, 22 skipped, 13
  existing warnings**. Initial database suite **121 passed**; final PostgreSQL
  dataset/migration/API/CLI/concurrency suite **24 passed** after review fixes.
  Ruff, whitespace, Compose config, Alembic0041 head and offline upgrade SQL
  (**1295 lines**) passed. Frontend source was unchanged in this slice; prior web
  results are not represented as newly run here.
- Remaining R3 work: existing Forward observation/outcome revision and dependency
  recovery, plus invalid in-flight wallet/token dependency binding. R6/R7/R9–R14
  remain OPEN, including durable after-commit effects, SSE, complete UI freshness,
  dense-history bounds, clean-checkout closure, access controls, full topology and
  real-provider/risk-field compatibility. The overall goal remains ACTIVE.
- Cleanup audit verified zero test-owned schemas and removed the labelled isolated
  PostgreSQL container/volume. Automatic approval rejected the combined recursive
  temporary-environment cleanup with "blocked by policy" and no detailed reason;
  generated .venv/uv.lock/offline SQL remain outside the scoped commit. Existing
  untracked product/data files are preserved.

## Forward observation/outcome reconstruction (2026-11-06)

- Continued R3 with immutable LiveSignalObservation/LiveSignalOutcome revisions
  under the shared epoch/manifest/dependency/publication contract. Canonical reorg
  and same-epoch source corrections withhold old entries/returns immediately;
  source values remain auditable. Outcomes retain a stable first-observation root
  while binding the actual input revision. Rebuilds preserve original T0, frozen
  policy, entry pair and evaluation_as_of without selecting a new profitable cohort.
- Cohorts explicitly mean recorded-transition-cohort-v1. Revised canonical inputs
  do not rewrite the originally recorded class. Withdrawn canonical discovery or
  signal yields an UNAVAILABLE audit observation. Unknown legacy parameters/scores
  are not invented; legacy-forward-upgrade-v1 is explicit and old values remain.
  Unsupported recipes are excluded rather than certified by newer code.
- ForwardEvaluationJob separates mutable retry state from immutable result values.
  Atomic horizon enqueue, bounded cyclic repair, pending retry, savepoints and
  job-first locking cover restart, rollback, failed-neighbor isolation and concurrent
  claims. Missing original entry prices can recover after delayed ingestion while
  preserving T0. Completed-result invalidation/rebuild repairs job pointers and
  reopens UNKNOWN results for retry; a replaced result cannot strand a COMPLETE job.
- Exact normalized price/Pair sources and derived dependencies are captured. Entry
  liquidity/market cap come from explicit USD snapshots of the frozen entry pair,
  preventing another pool's values from fabricating rug/liquidity-failure labels.
  Price-path-v2/forward-v2 arithmetic and bounded endpoints remain unchanged.
- Performance reads matching current observation/result revisions in one SELECT,
  fences epoch changes and reports withheld/rebuilding, observed, pending, not-due
  and withdrawn states. Available-only metric denominators stay explicit. Existing
  snapshot audit endpoints cover both Forward tables; /api/research/forward-jobs
  pages operational provenance without certifying historical results current.
- Migration0042 preserves/quarantines real pre-lineage observations and legacy-v1/
  forward-v2 values, reconstructs corrected forward-v2 returns after restart and
  refuses lossy downgrade once research/jobs exist. No production migration or live
  provider request ran. Existing untracked product/data and prior temporary files
  are retained outside this scope.
- One fresh read-only reviewer confirmed two P1s with small SQLite reproductions:
  competing empty first lookups could move T0; COMPLETE→UNKNOWN repair failed to
  reopen its job. Both have regressions and fixes. Root-directed PostgreSQL tests
  also reproduced/fixed repair-cursor serialization and cross-pair liquidity.
  This was one review and one material fix pass, with no implementer agents.
- Final implementation backend: **667 passed, 26 skipped, 13 existing warnings**.
  Isolated PostgreSQL comprehensive suite: **128 passed, 4 existing warnings**,
  covering Forward/historical/Radar public paths, concurrency/rollback/restart and
  all seven historical migration fixtures (including calculation-only regressions).
  Full Ruff(src/tests), whitespace, Compose configuration, Alembic0042 head and
  offline migration SQL (**1339 lines**) passed. Frontend sources were unchanged;
  previous web results are not presented as newly run in this slice.
- Cleanup verified zero owned test schemas before removing the labelled isolated
  PostgreSQL container/volume. Prior temporary .venv/uv.lock/offline SQL and existing
  untracked product/data files remain outside the scoped commit.
- R3 remains OPEN for exact invalid in-flight wallet/token dependency binding and
  its final transitive integrity audit. R6/R7/R9–R14 remain OPEN, including deferred
  projection effects/outbox, SSE, complete UI behavior, dense price/input bounds and
  repeated UNKNOWN retention, reproducible source/CI, access controls, full topology
  and provider/risk-field compatibility. The overall goal remains ACTIVE.
- Follow-up audit found a concrete additional wallet/token freshness defect in
  IntelligenceAggregationService's raw-source queries. An isolated in-memory
  SQLite reproduction retained TradeEvent.value_usd=10 in a reader, committed a
  second-session correction to20 plus canonical invalidation, then refreshed the
  reader. Persisted SQL value was20 while the newly visible epoch2 token flow and
  manifest still contained10. These queries lack populate_existing. This remains
  a required R3 fix alongside exact computed-profile/publication-winner binding;
  prior snapshot/Forward passes do not certify wallet aggregation freshness.

## Wallet/token source integrity and USD-v2 (2026-11-06)

- Fixed the concrete retained-identity-map failure in all/full, token-scoped and
  wallet-scoped aggregation. Trade/Pair rows refresh before replay/manifest reads;
  epoch fences reject a changed generation without holding the research epoch
  writer lock. A real two-writer regression preserves the newer canonical winner
  while the old computation aborts, with no falsely bound token revision.
- Token aggregation now uses the actual published wallet score/confidence. Epoch,
  input digest, profile body, score and publication-status mismatches reject the
  attempted binding. Manifests contain normalized trades and exact scoring inputs
  in addition to raw source values and wallet snapshot references.
- Added independent canonical-wallet-usd-v2/canonical-token-usd-v2 input policies.
  Score-v2 weights and the profile-v1 container remain identified separately. Native
  amounts, generic quote prices, cached metadata USD amounts and future/cached
  creation/outcome labels cannot supply canonical USD accounting. Missing valuations
  use neutral score inputs with valuation-based confidence; exported measurements
  remain null/UNKNOWN. Known USD cash-flow subtotals do not require matchable inventory.
- Nullable token counts/flows retain UNKNOWN through ORM storage and API JSON;
  incomplete sides make net flow unknown, while observed zero stays zero. Unknown
  ROI sorts last. Repositories/API readers reload persisted JSON. Radar recognizes
  healthy CHAIN_EVENTS producers and emits UNKNOWN rather than the string "None"
  for unavailable fields. Raw API trade payloads use dedicated USD values/prices.
- Migration0043 quarantines unsafe wallet/token recipes and existing transitive
  consumers without rewriting old values or invalidating unrelated risk recipes.
  Isolated old0042 data rebuilds through wallet→token→dataset/Radar/Signal/Forward
  under the corrected policy at original cutoffs. Unsupported input recipes stay
  explicit; downgrade refuses unknown flows, USD-v2 research or reconstruction work.
- One fresh read-only reviewer confirmed three material issues: Radar null-string
  propagation, known cash flow lost when quantity was missing, and non-object
  auxiliary JSON aborting replay. All received regressions/fixes. Root checks also
  corrected healthy producer admission, null ROI ordering and retained JSON reads.
  There was one review/fix pass and no implementer agents.
- Final backend **688 passed, 28 skipped, 13 existing warnings**; full Ruff(src/tests),
  whitespace, Compose configuration, Alembic0043 head and offline SQL **1357 lines**
  passed. Frontend source was unchanged; no prior web result is claimed newly run.
- Isolated PostgreSQL comprehensive run: **141 passed**, with one old Radar/Signal
  migration assertion expecting UNKNOWN after a healthy CHAIN_EVENTS reconstruction.
  Updated that assertion to require canonical token flow10 and its actual published
  score while retaining LOW confidence; the isolated migration recheck **1 passed**.
  The two runs jointly verify the selected142 checks on unchanged implementation.
  The original live tool handle became unavailable after continuation; its process
  was verified absent and schemas empty before rerunning the full selection with
  an external verification log. No missing handle or partial output was counted green.
- Cleanup verified zero owned schemas, then removed the labelled isolated database
  container/volume. Verification logs, prior temporary environment/lock/offline SQL
  and existing untracked product/data remain outside the scoped commit.
- R3 is still OPEN on the final reader/mutation audit. A concrete follow-up in an
  isolated SQLite API reproduction found POST /api/wallet/replay includes a trade
  with canonical=False/removed=True in its recurring-wallet output; that query lacks
  canonical_fact_conditions. Canonical filtering and the adjacent legacy research
  readers are the next required fixes, rather than treating materialization checks
  as proof for those endpoints. R6/R7/R9–R14 and the original overall goal stay active.

## Canonical legacy research readers (2026-11-07)

- Continued R3 with a shared read boundary for wallet replay, graph, clusters,
  profile/network, token smart-money flow and the BSC market-event list. Canonical
  flags, JSON reorg markers and known orphan/future PairCreated guards apply in SQL
  before source limits and wallet-to-token neighborhood discovery. Persisted ORM
  values are reloaded; orphaned activity cannot discover a neighboring token or
  consume a replay slot. Empty user cohorts do not read unrelated chain facts.
- Each request freezes one UTC cutoff and captures its source epoch before reads.
  Its inline research_provenance contains the exact trade/pair/wallet snapshot
  values, selected/sentinel IDs, parameters, graph limits and digest. Profile and
  network use one session/cutoff, with the network cutoff explicit when the selected
  profile is older. Epoch changes and withdrawn selected wallet snapshots fail
  closed with HTTP409 REBUILDING after calculation; readers hold no epoch writer
  lock. PostgreSQL checks use independently committed writer connections.
- These manifests are ad-hoc response evidence, not durable stored research
  revisions. Retain the full response for a historical audit. Existing snapshot
  revision storage/rebuild semantics remain separate; this slice does not claim
  automatic historical retention or retraction of downloaded API responses.
- Monetary flow accepts finite nonnegative explicit USD values only, preserves
  observed zero and exposes missing-value/classification counts. Missing USD makes
  the affected side and net unknown. Unclassified wallets or a truncated source
  prevent exact totals and direction claims; known_inflow/outflow are partial
  subtotals. Latest wallet classification respects the cutoff and current snapshot
  visibility and is restricted to the bounded flow's participants.
- User-supplied replay cohorts retain their tokens and empty entrant results and
  are labeled USER_SUPPLIED_UNVERIFIED. Source and graph clipping are disclosed.
  One fresh read-only reviewer reproduced a missing graph cluster-cap disclosure:
  different caps produced different node sets with an identical manifest digest
  and truncated=False. A failing two-cluster API regression now requires the cap
  in response-selection provenance and a cluster_node_limit truncation reason.
- Final isolated PostgreSQL selection: **69 passed**, including two independently
  committed reconciliation cases, latest-visible snapshot selection, cached-input
  refresh, HTTP JSON serialization and the cluster-cap regression. The runner uses
  the project's installed psycopg2 driver and a fresh schema per test. Verified
  zero owned schemas/connections before removing the labelled test container and
  its volume. Existing local/untracked product data and verification logs remain.
- Final full backend run: **713 passed, 30 skipped, 13 existing warnings**. Full
  Ruff(src/tests) and whitespace checks passed. The two PostgreSQL-only reader
  concurrency cases skipped in the default run are included in the 69-pass
  PostgreSQL selection. No frontend implementation or schema migration changed.
- The user authorized ongoing GitHub synchronization during this batch. Verified
  commits are pushed to origin/codex/research-integrity; no merge or deployment is
  part of that instruction.
- R3 remains OPEN for the final source-reader/mutation audit. Presentation metadata
  queries and old compatibility paths still need explicit review before closing
  that requirement. R6/R7 event projection/outbox/SSE, R9/R10 workflows and R11–R14
  operation/reproducibility/access/Compose acceptance remain active.

## Transactional Radar effects and durable event relay (2026-11-07)

- R6 implementation now shares final-state effects across normal and deferred
  ingestion. Deferred chunks attach every eligible canonical proof before one
  refresh per affected candidate; the previous last-row selection lost earlier
  qualifying events. Both routes schedule audit work, retain the first sampled
  discovery-time baseline and evaluate enabled rules. Intermediate refreshes remain
  coalesced in the deferred route. Reorg revalidation also records the withdrawn
  candidate's update through the same effects boundary.
- Alembic0044 adds RadarEventOutbox. Immutable original event IDs, type, subject,
  payload and occurrence time are inserted in the owning database transaction.
  Delivery state is separate. Business rollback leaves no sendable envelope;
  payloads are copied before later ORM changes. Duplicate effect keys must refer
  to the same payload. Downgrade refuses to discard any event/delivery history.
- Audit-start is committed atomically with its claimed job, before releasing the
  job-row lock. Failed audit computation rolls back its whole work transaction
  before recording failure, recovering even a failed SQLAlchemy session and
  discarding partial result/events. Caller rollback after success retains only the
  already-committed start. Audit lifecycle/feature events and manual rule delivery
  use the outbox; production database mutation callers no longer dispatch
  fire-and-forget tasks. The unused nowait mutation escape hatch was removed.
- The event-relay role/Compose service claims bounded batches with SKIP LOCKED,
  commits claims before networking and uses 60-second expiring lease tokens to
  fence completion/retry. Failed transports persist only safe exception class
  codes with bounded backoff. Cancellation leaves a recoverable committed lease.
  Local success followed by Redis failure retries the same envelope/event ID;
  delivery is at least once. DELIVERED records transport publication, not browser
  receipt. Historical transition payloads are hints to refetch current canonical
  state; SSE disconnect/replay remains R7.
- Read-only /api/research/event-deliveries provides bounded cursor pagination of
  envelopes and attempts/leases/status. Worker heartbeat/source health includes
  event-relay. Deployment instructions now name the actual Compose services and
  describe event ownership, retry, duplicate IDs and migration limits.
- One fresh read-only review was interrupted by the user's pause and resumed as
  the same review. It found a manual-alert channel regression: in-app-only or
  webhook-only rules would enqueue browser SSE messages. RED tests reproduced
  both cases; preserving the browser-channel guard made all three channel cases
  pass. No additional P0/P1/P2 findings were reported in that review.
- The pause preserved uncommitted source. Verification handles were no longer
  present on resume; saved terminal logs proved 727 backend checks and 63 selected
  PostgreSQL checks passed. The new migration test's singular research_state
  table name failed; it was corrected to research_states with an explicit fixture
  upsert. Those earlier runs are not substituted for final verification.
- Docker Desktop could not restart because its temporary sailor-ingest.sock could
  not be opened. Automatic approval rejected stopping that failed startup and
  deleting its temporary IPC file, reporting only blocked by policy. That action
  was not retried. Subsequent verification uses an independent temporary WSL
  PostgreSQL16 cluster and locally extracted Redis7 packages; system services,
  Docker data/configuration and provider credentials are not modified.
- That non-UTC PostgreSQL instance exposed a test assertion that replaced timezone
  information instead of converting the instant. The stored baseline remained at
  the correct original cutoff; the assertion now uses the shared UTC conversion.
  A separate default-environment health test exposed asyncpg authentication errors
  escaping check_database. A RED driver-error regression now requires unavailable,
  and the narrow PostgresError catch fixes the endpoint failure. The paper-mode
  API test stubs infrastructure checks instead of contacting developer services.
- Final PostgreSQL/Redis selection: **69 passed** on an independent PostgreSQL16
  instance with a non-UTC server timezone and Redis7. The real Redis test starts
  a separate API consumer process and proves a rolled-back envelope stays private
  while a committed envelope arrives with the original ID/body. Migration0044
  upgrades from0043, preserves source epoch7, permits an empty downgrade and
  refuses loss of committed history. Claims across independent connections skip
  locked rows; a publisher can acquire its claim row from another connection,
  proving the relay releases DB locks before networking.
- Cleanup confirmed zero owned schemas, no other verification DB connections and
  no API test subscriber, then stopped only the temporary PostgreSQL and Redis
  instances. Temporary packages/data/logs remain for audit. The Docker startup
  fault remains an environment limitation; this does not constitute R14 full
  Compose deployment/restart acceptance.
- Final backend run: **732 passed, 33 skipped, 13 existing warnings**. Full Ruff,
  whitespace, Compose configuration and the event-relay CLI role checks passed.
  Alembic0044 is the sole head; offline upgrade SQL generated 1383 lines. R6 is
  complete at its final-state/committed-event scope. R3 final audit, R7 reconnect,
  R9/R10 workflows and R11–R14 remain OPEN; the original programme stays active.

## Bounded SSE recovery and canonical browser resnapshot (2026-11-07)

- R7 implementation replaces QueueFull during >256-event replay and silent
  slow-client loss with explicit STREAM_RESET controls. Retained cursors replay
  matching events only when they fit; unknown/expired cursors require resnapshot.
  History/queue sizes stay bounded. A pending reset coalesces later events into
  its latest resume cursor until consumed. Controls bypass business filters, and
  subscription attachment/history lookup share one lock. Lazy registration fixes
  an abandoned-before-iteration response leaking a subscriber.
- SSE control IDs use the actual resume cursor, or clear Last-Event-ID when no
  trusted cursor remains. A new API connection requires an initial snapshot.
  Broker loss/reconnect invalidates old history and communicates link availability;
  the Redis bridge retries with a bounded delay and closes clients/pubsubs on every
  attempt. Invalid/oversized frames reset continuity instead of killing the feed.
  Neither bounded memory replay nor Redis publication is an exact durable event log.
- The browser requests its supported business types, consumes controls separately,
  bounds recent-ID dedup at1024 and treats all domain messages as refresh hints.
  Historical/retried payloads can no longer overwrite canonical risk, money or
  priority. Reset/open invalidates in-flight candidate responses by generation,
  triggers a coalesced REST refresh and displays recovery/failure/link states.
  Selected-detail recovery refreshes research without reloading annotations or
  erasing drafts. Failed detail recovery hides unrefreshed research and offers retry.
- UI/UX skill guidance for error recovery was applied to explicit status text,
  accessible live feedback and retry actions while preserving the existing layout.
  Previously untracked application entry points and their existing source
  dependencies are included so the published recovery client is buildable.
- One fresh read-only review found two protocol faults: Redis eager UTF-8 decoding
  could terminate the bridge before validation, and NUL/lone-surrogate IDs could
  poison both business and reset SSE frames. RED regressions reproduced both.
  The bridge now validates raw bytes and also recovers decoder-level failures;
  cursors must be UTF-8 encodable and exclude NUL/CR/LF before retention.
- Real PostgreSQL/Redis verification: **30 passed**. A separate real HTTP API
  process receives a committed event, rejects malformed UTF-8 with a reset,
  discloses CLIENT KILL of its Redis subscription, reconnects, rejects the old
  replay cursor and receives the next event. The committed-outbox consumer also
  handles the new control handshake. No live chain/provider calls are involved.
- Frontend **50 tests passed**, typecheck and production build passed. A separate
  export of all38 intended frontend source/build inputs ran fresh npm ci, build
  and the same50 tests; SHA256 comparison confirms it matches the worktree.
  Initial offline installation lacked one cached package, so normal locked install
  was used. Node24.14 emits the existing jsdom engine-floor warning; runtime and
  dependency version policy and CI remain OPEN R12 work, not certified here.
- Final backend run: **743 passed, 34 skipped, 13 existing warnings**; full Ruff
  and whitespace passed. The post-review Redis/PG selection is30 checks, with all
  decoder/cursor fixes included. Verified zero owned schemas, no other test DB
  connections and no API Redis subscriber before stopping the owned temporary
  services. Export/dependency files and verification logs remain outside the commit.
- R7 is complete at its bounded replay/resnapshot contract. R3 final audit,
  R9/R10 workflow, R11 bounded operation, R12 reproducibility/CI, R13 access and
  R14 full topology/provider acceptance remain OPEN. The overall goal stays active.


## Complete candidate browser and continuous research refresh (2026-11-07)

- R9 selection now shares SQL filters, sorting, facets and page selection over the
  complete candidate subject index. Search includes literal case-insensitive token
  identity, canonical pair address and platform; all four sort keys have stable
  ties and ascending/descending support. Counts remain global on empty or
  out-of-range pages. Only the selected page expands evidence; the505-subject
  regression hydrates exactly5 rows for offset500/limit50.
- Selection binds current projection/signal subjects, revision visibility and a
  single UTC cutoff. Epoch/revision fences fail with HTTP409 REBUILDING if canonical
  corrections commit during hydration. A real independent PostgreSQL writer can
  commit its invalidation while the reader is open, and the reader rejects the
  stale response. GET candidate/detail readers no longer commit read transactions.
- Known zero and UNKNOWN are distinct. Missing/invalid priority remains UNKNOWN,
  and an explicitly zero minimum excludes unknown metrics. Finite scientific
  values preserve small values and three-digit exponents without fixed-scale
  rounding; unsupported overflow/underflow/nonfinite metrics stay UNKNOWN under
  the documented browser numeric contract. Missing 5m volume alone may use the
  legacy volume key; explicit null/unknown/zero never triggers that fallback.
- The browser requests50-row server pages with global facet counts, all supported
  filters and sort directions. Query changes reset offsets, abort/discard obsolete
  reads and recover a shrinking final page. Discovery includes stale observations
  with explicit freshness filtering; Watch can include unscored non-high-risk
  subjects without converting unknown scores to zero.
- Candidate/detail/statistics polling runs every15s and coalesces requests. Draft
  annotations and their focused DOM node survive ordinary research refreshes.
  Background failures label retained research stale; strict recovery/HTTP409 hides
  invalid research. Statistics explicitly distinguish loading, failed reads,
  rebuilding/unavailable cohorts and genuine CURRENT zero samples. Read time and
  observation time have separate labels.
- One fresh read-only review found three Important issues: small-number/exponent
  normalization, explicit zero priority minimum, and rebuilding statistics shown
  as current empty results. All three were reproduced RED and corrected in one
  fix pass; the targeted backend/frontend cases are GREEN. No deferred minors.
  The plan records all considered scope decisions and their practical costs.
- Final real PostgreSQL16 verification: **68 passed**, including complete sorting,
  literal search/global facets, page-bounded hydration, finite/unknown values,
  separate committed canonical invalidation and existing signal/snapshot reader
  regressions. Verified zero owned schemas and other DB connections, then stopped
  only the owned temporary PostgreSQL instance. No provider calls or production
  data writes occurred; logs and temporary database files remain outside Git.
- Frontend **62 tests passed**, TypeScript and production build passed. Existing
  Alpha/CopyAddress act warnings remain. Browser QA with synthetic505-subject
  data verified page2, search for subject505 with page reset, known-zero vs unknown
  amounts and a390px card layout. This is local UI acceptance, not live-chain or
  full-Compose acceptance.
- Final backend: **768 passed, 35 skipped, 13 existing warnings**. Full Ruff and
  whitespace checks passed. R9 is complete at the current-state candidate browsing
  and refresh contract. R3 final audit and R10–R14 remain OPEN; the original goal
  stays active. Latest user authorization continues verified pushes to
  origin/codex/research-integrity without merge or deployment.


## Explicit analyst scope and safe manual writes (2026-11-09)

- R10 implements persisted TOKEN/GLOBAL alert targets, rather than inferring a
  token from a name. BSC addresses normalize at the write boundary; automatic and
  explicit evaluation reject another token/chain before creating history. Partial
  updates retain omitted scope/target fields; GLOBAL expansion requires an
  explicit choice. New UI defaults to the current token and displays its full
  address; success names the submitted scope and returned rule ID.
- Annotations now require expected_revision:0 for a currently absent annotation,
  or a positive current revision for updates/deletion. Missing preconditions
  return428 and conflicting edits return409. Deleted contents clear while a
  version tombstone prevents reusing a positive revision after re-creation.
  Independent PostgreSQL writers produce exactly one successful create/update;
  the losing request cannot overwrite it.
- Rule creation accepts a unique UUID request identity. Concurrent retries return
  one rule without rolling back unrelated writes. Request IDs are immutable on
  PATCH. Rule deletion hides/disables a tombstone and retains history/request
  metadata, so delayed creation retries cannot resurrect it. Browser retry IDs
  persist for recent256 definitions in a tab, through detail closure/reopening,
  parameter revisits and reload when session storage works; no notes or credentials
  enter this cache. Legacy callers without IDs remain non-idempotent.
- AnalystWorkflow is independent of research refresh and owns loading/drafts/save
  outcomes. A failed read blocks editing/saving; only404 establishes empty state.
  Favorite/note/tags use one explicit save. Edits made during submission remain
  visibly unsaved after that submission completes. Conflicts preserve the draft
  while an explicit latest-version read provides comparison; the preservation
  intent survives failed recovery reads even when dirty comparison is false.
- Migration0045 preserves old notes at revision1 and old rules as GLOBAL, adds
  native boolean defaults, scope checks and UUID uniqueness, and refuses loss of
  target/revision/request/deletion metadata on downgrade. SQLite full-suite checks
  initially exposed unsupported ALTER constraints; the batch-alter correction is
  GREEN. A separate raw legacy-insert RED regression proved quoted false defaults
  could hide annotations on SQLite; native SQL defaults fix it.
- One fresh read-only review was interrupted by a usage limit, then resumed on the
  same reviewer and returned two Important findings: omitted PATCH scope broadened
  TOKEN to GLOBAL, and failed conflict-recovery retry discarded a preserved draft.
  Both were reproduced RED and fixed GREEN in one review pass. The reviewer raised
  retry-lifetime policy for a ruling; a further RED closure/reopen case now requires
  persisted tab-local identities. All considered scope decisions/costs are recorded
  in the plan; no deferred minors.
- Browser QA used the actual analyst HTTP router and a separate PostgreSQL schema,
  with synthetic market context and no live providers. It saved revision1, an
  independent writer committed revision2, the UI rejected the old draft revision,
  retained it during comparison and saved revision3. Creating TOKEN and explicitly
  GLOBAL rules persisted exactly two distinct targets. Screenshot/evidence files
  stay outside Git. The owned QA server exited and dropped its schema.
- Final frontend **76 tests passed**, TypeScript/production build passed. A valid
  checksum-cased target RED regression also showed successful canonical lowercase
  responses could be misreported as failed; the workflow now normalizes BSC identity
  before requests, cache signatures and scope validation.
- Final backend **781 passed, 39 skipped, 13 existing warnings**; full Ruff and
  whitespace checks passed. Final PostgreSQL16 selection **66 passed**, including
  migration0045/0044 history preservation, guarded downgrade, independent writer
  races and existing signal/outbox regressions. The raw-SQL default test explicitly
  selects its owned schema on PostgreSQL; its initial missing-search-path failure
  was a test fixture routing error, not a missing product table.
- Verified zero owned verification schemas and other DB connections after browser
  and test teardown, then stopped only the owned foreground PostgreSQL process.
  R10 is complete at the explicit scope/conditional-write/visible-outcome contract.
  No merge, deployment, production database write or live-provider operation occurred.
- Environment evidence: an earlier background WSL database disappeared before a
  selected run (connection refused and missing /tmp data), causing setup errors;
  these are not counted as product checks. Verification resumed from a retained,
  task-owned home-directory database with an explicitly foreground-owned WSL
  process. No production database, Docker configuration or live provider was changed.
- Remaining audit: R13 must harden analyst access and condition operand validation;
  R3 must review manual evaluation provenance; R14 must verify delivery semantics
  and the full isolated topology. R3 and R11–R14 remain OPEN under the original goal.


## Due-work runtime and exact VWAP (2026-11-09)

- This batch advances R11 without closing its wider retention/publication/rebuild
  obligations. Only due pending/tracking Alpha snapshots load trade inputs; a
  completed/not-due regression requires zero trade queries. Pairs/jobs/receipts
  and exact pair/token/time trade ranges share batch reads, with at most200 range
  predicates per SQL query. Manifest receipt reuse retains the existing generation,
  canonicality, query-scope and evaluation-cutoff proof checks. Forward price ranges
  use the same predicate bound; valid dense data inside each horizon is retained.
- VWAP resolves every eligible trade once, sorts timestamps and slides exact
  scaled-integer totals. Equal timestamps enter together, (start,end] boundaries,
  earliest ties, invalid-amount anchors, HUMAN notional gates and30s-before60s
  preference remain explicit. Large expired amounts cannot round away a tiny
  surviving amount. Final division still follows Decimal context. Independent
  rational-reference and RAW uint regressions cover arithmetic and basis isolation.
- Corrected rounding edges create a new recipe: experimental-v4 / alpha-shadow-v3,
  historical alpha-labels-v2, with exact arithmetic identity in policy/manifests.
  Old v3 scores/manifests remain stored under their own recipe and outside the new
  denominator. Canonical invalidation covers archived recipes too; reconstruction
  emits the active recipe without pretending to rerun an archived algorithm.
- Cyclic bounded registration remains necessary for sequence/commit-order inversion.
  PostgreSQL repair ownership is schema/component/version scoped and transaction
  bounded; skip-locked cursor access plus nonblocking advisory ownership prevents
  both warm repair and first-startup registration from stalling independent work.
- Forward/Alpha cycles report phase boundaries, stage/total duration, fixed error
  categories and only successfully committed counters through durable heartbeats.
  Independent API sessions read a whitelisted research summary from /health.
  Cancellation rolls back and propagates; report failures cannot erase a successful
  source commit. Reports are stage-boundary observations for the current instance,
  not continuous CPU timers or a cross-restart latency history/SLO.
- One fresh read-only review found one Important malformed-heartbeat validation
  issue. List/dict enum fields and a UTC-overflow timestamp were reproduced RED
  through the actual health endpoint, then fixed GREEN. No Critical or deferred
  Minor findings. The plan records the accepted boundaries and remaining costs.
- Real non-UTC PostgreSQL exposed eight old assertions that replaced timezone
  labels instead of converting instants. They now compare utc() values; product
  timestamps were not changed. A RAW regression fixture also needed an explicit
  BUY direction, as required by the existing amount-recovery contract.
- Synthetic dense VWAP evidence:400 trades previously resolved the basis160000
  times (0.6652s); the new path resolves400 times (0.002209s).10000 trades resolve
  exactly10000 times (0.055424s). These local synthetic timings are not production
  throughput guarantees, and no dense-window facts were truncated for the benchmark.
- Final backend: **817 passed, 42 skipped, 13 existing warnings**. The final
  PostgreSQL16 selection passed113 runtime/concurrency/reconstruction checks; the
  separate final historical upgrade/downgrade check passed1 test. Its v4 recipe0
  is rebuilt as revision1 before verifying the Alpha downgrade refusal. Global
  invalidation correctly reaches the newer Derived guard first, so this fixture
  uses the targeted Alpha helper to isolate the intended guard. Broader revision0
  manifest archival/downgrade policy remains R12, not a claim of this check.
- The changed Forward timestamp-assertion file also passed17 SQLite checks with4
  skips. Frontend source is unchanged in this batch; its76 tests and TypeScript/
  production build passed during batch verification. Existing frontend act warnings
  remain. Full Ruff and whitespace checks passed.
- Verified zero owned schemas and other database clients, then stopped the owned
  foreground PostgreSQL instance and observed its terminal exit. Temporary evidence
  remains outside Git. No provider call, production write, merge or deployment
  occurred. R3 and R11–R14 remain OPEN; the original programme continues, with
  scoped verified commits pushed to origin/codex/research-integrity as authorized.


## Independent reconstruction and durable health (2026-11-09)

- R11 reconstruction previously claimed jobs with skip-locked SQL only after
  blocking on all repair cursors. Warm and cold PostgreSQL reproductions failed
  on the cursor/initial-insert wait. Repair now uses schema/kind-scoped nonblocking
  ownership and skip-locked cursor reads, retaining cyclic wrap for late commits.
  A busy repair owner no longer prevents independent durable jobs from completing.
- Each reconstruction job uses a SAVEPOINT and a temporary PostgreSQL lock_timeout
  capped at250ms, preserving a shorter existing nonzero setting. Success restores
  the exact caller setting; rollback restores it after a failed statement. A busy
  job stays PENDING with a60s retry and fixed exception-category last_error, while
  independent jobs can commit. Real dependency contention verifies SQLSTATE55P03
  (lock timeout), rather than a whole-statement timeout. This is a per-lock bound,
  not a query/CPU/whole-batch SLO or permission to truncate facts.
- A root publication identity probe detects existing contention before loading
  facts, then rolls back its inner SAVEPOINT to release the temporary lock. Actual
  publication reacquires the identity and checks the latest winner. This matters:
  the first implementation held the root through calculation. An independent live
  publisher reproduced an Important regression RED; the released-probe fix passes
  GREEN and requires the fresh live policy/value to win over the older rebuild.
  The live producer publication protocol is unchanged.
- rebuild_due retains its integer return and can report repair/root-probe/rebuild
  seconds and examined/completed/deferred/unsupported/stale/superseded job counts.
  The worker adds COMMIT/IDLE stages and persists counts only after actual commit.
  /health exposes a separate whitelisted research_rebuild profile from committed
  heartbeats. Pulses update elapsed time during async waits and are joined before
  terminal reports. No arbitrary heartbeat fields or exception text are exposed.
- Real PostgreSQL cancellation interrupts a recipe while its publication/job/cursor
  locks are held, rolls back attempt changes, reports STOPPED without committed
  counters and allows a new cycle to finish the same jobs. Separate API/session
  readers observe the persisted result/timing. Failed heartbeat writes do not undo
  a successful source commit. Existing forward-worker health remains compatible.
- One fresh read-only reviewer reported no Critical/Important/Minor findings. The
  parent independently identified and fixed the root-lock hold regression above
  during the same final fix pass; no second review or deferred minors. The plan
  records every considered scope ruling, including cooperative-worker ownership,
  old-version/DDL rollout limits, idle-sleep shutdown reporting, per-instance recent
  telemetry and the broader acceptance requirements that remain OPEN.
- Synthetic SQLite reconstruction retains2000/10000 canonical input rows, excludes
  the orphan, includes its replacement, preserves the old manifest digest and keeps
  missing USD fields null. Five no-op cycles create no extra revision (history stays2).
  Final local rebuild+commit took0.539/1.900s; these are synthetic observations, not
  production throughput or storage-growth guarantees. The first benchmark fixture
  omitted a required trader address; correcting that fixture changed no product code.
- Final backend **831 passed, 55 skipped, 13 existing warnings** after the probe
  fix. Final real PostgreSQL16 selection **86 passed**, including warm/cold repair,
  root/dependency contention, live publication during rebuild reads, source commit/
  rollback/cancellation, durable health, late commits and existing derived/Forward
  reconstruction. Full Ruff and whitespace checks passed. A single old derived
  API test assertion now converts non-UTC instants with utc() instead of relabelling
  tzinfo; no product timestamp semantics were changed. Frontend source is unchanged.
- Confirmed zero owned verification schemas and other database clients, stopped
  the owned foreground PostgreSQL instance and observed its terminal exit. Evidence
  stays outside Git. No provider call, production write, deployment or merge was
  performed. This verified batch advances R11; canonical anchors/provenance,
  selective invalidation, dense storage and whole-path throughput/latency still
  require closure. R3 and R11–R14 remain OPEN under the complete original objective.


## Lossless canonical-anchor retention (2026-11-09)

- Canonical anchors are no longer required to stay as one indexed hot row per
  historical height. Old complete512-height buckets move into bounded compressed,
  immutable segment revisions, retaining exact hash/UTC observation time and a
  SHA256/format/row-count check. Missing heights stay missing. Current lookup merges
  the latest requested archive buckets with hot corrections by observation time;
  old scans still see a known fork conflict after compaction. Older authoritative
  responses cannot replace a newer archived observation. Old segment revisions
  survive corrections; receipts, manifests and research history are not TTL-deleted.
- The hot target defaults to4096 blocks and expands to retain the full configured
  reconciliation window. One cycle handles at most one complete512-height bucket;
  after backlog drains, at most511 incomplete-bucket heights can exceed the target.
  Undrained backlog and total immutable evidence can grow. This is not a fixed disk
  cap, nor a claim that PostgreSQL allocated files shrink after deletion.
- Copy/checksum/new segment/conditional hot deletion use the same publication fence
  and commit atomically. A real unexpected external correction between copy/delete
  rejects the archive and preserves the correction/all hot rows. Actual cancellation
  after segment write but before delete leaves1536 hot rows/zero segments; a new
  cycle then succeeds. Corrupt bytes, digest/schema/time errors, oversized/trailing
  streams and parser nesting faults fail closed without becoming missing evidence.
- Canonical publication is batched: the600-height RED case originally issued1802
  SQL statements; the GREEN case requires at most15. Existing single-row insert_once
  callers remain compatible with its new positional mapping/list support. There
  is no score/label recipe change: the same canonical fence values are retained.
- Maintenance runs as an anchor-archive phase cohosted with research reconstruction,
  in a separate small source transaction. Its own whitelisted /health profile
  publishes archive/count/state fields only after source commit. Metadata audit
  pagination excludes payload columns; a separate detail verifies one segment and
  explicitly distinguishes latest archive revision from current hot/chain truth.
- Migration0046 is additive, retains hot rows, refuses any evidence-losing online
  downgrade and refuses unverifiable offline downgrade. Updated readers/writers
  require migration-first/stopped-old-binary rollout. A real head0046 upgrade exposed
  old hardcoded current-head checks; a shared test helper now derives the graph head
  while retaining every explicit historical target. The full PostgreSQL integration
  selection passed24 tests/2 live-network skips, including every migration test.
- One fresh read-only review found one Important/no Critical/Minor: a precommit
  heartbeat could hold the global fence for the2s reporting deadline. Real blocked
  telemetry/independent-publication cases were RED then GREEN for ARCHIVED and IDLE
  cycles. The await now happens only before acquiring or after releasing the fence.
- The parent regraded an archive-specific part of a declined lifecycle concern:
  corrupted evidence made reconciliation record failure while retaining its source
  transaction. A real independent publisher reproduced the stall RED. The new
  reconciliation-owner context rolls back before releasing the shared AsyncSession
  owner, preserving fail-closed reads and independent progress. The fix pass verifies
  normal reconciliation/cancellation too; it does not certify every older ingestion
  exception/cache path. No second review or deferred Minor findings.
- PostgreSQL realistic-hash benchmark:16384 hot rows drain in24 moves to4096;
  another16384 observations drain from20480 hot rows in32 moves back to4096. A deep
  correction retains57 total segment revisions; five no-op cycles add none. Binary
  payload1251692 bytes represents3204045 bytes of deterministic JSON. Local2.744/
  3.576s drain times are synthetic evidence, not a production throughput SLO. Initial
  16384 hot logical rows occupy1966077 bytes and final4096 occupy491517 bytes; these
  different populations are not a before/after total database-file comparison.
- Final PostgreSQL runtime/coverage/collector/research/derived selection **135 passed**
  after both lock fixes. The plan records all review rulings: total storage/production
  sizing remains open, consistent backup/rollout/full topology remains R12/R14,
  raw SQL administration is outside ORM immutability, checksum is not signed source
  authenticity, and read-only review did not independently execute the test fixtures.
  Frontend/live-provider acceptance is unchanged. See [anchor retention](CANONICAL_ANCHORS.md).
- Final backend after both lock fixes: **855 passed, 64 skipped, 14 warnings**.
  Warning categories are existing Alembic configuration/FastAPI/Starlette
  deprecations and SQLite expression-index reflection; deriving migration head
  adds an Alembic configuration warning emission, not a runtime failure. Ruff and
  whitespace checks passed. Final PostgreSQL135/complete integration24 pass counts
  above remain the authoritative scoped database evidence;2 live-network tests
  intentionally remain skipped. No frontend source was changed in this batch.
- Verified zero owned test schemas and other database clients, stopped the owned
  foreground PostgreSQL process and observed successful terminal exit. Temporary
  verification/benchmark evidence is kept outside Git; older user data/releases
  remain untouched. No deployment, production migration/write or provider call
  was performed. R3 and R11–R14 remain OPEN; full dense provenance storage,
  selective invalidation and whole-path throughput/latency still require closure.


## Reproducible checks and delivery (2026-11-09, verification in progress)

- Runtime/dev dependency locks pin existing tested Python versions, conditional
  platform dependencies and build tools with hashes. The stdlib checker detects
  stale declarations, altered lock bytes and npm direct-version drift while
  accepting Git line-ending conversion. Explicit generation uses pinned uv0.12.5
  and a temporary derived build input; no project uv.lock/.venv is modified.
- Verification no longer installs or resolves dependencies. Clean Windows3.13 /
  Linux3.12 installations and pip check passed. Exact npm specs preserve the prior
  resolved graph. Node24.15.0/npm11.12.1 is required by the observed jsdom engine
  boundary; clean frontend76tests and production build passed on both platforms.
- Source packaging uses tracked inputs in a checkout, refuses failed/mismatched
  Git discovery, preserves existing outputs, verifies per-file hashes and fixes
  ZIP entry ordering/timestamps. Exported sources outside a checkout remain
  supported without Git. Wheel checks verified47migration Python files and legacy
  static resources; an independent Linux runtime-lock-only wheel install and CLI
  check passed. Docker copies required configs and frontend dist and installs
  locked dependencies before first-party code without dependency resolution.
- New CI includes Ubuntu3.12/3.13, Windows3.13, actual PostgreSQL16/Redis7, frontend,
  source/wheel and digest-pinned runtime/web image builds and HTTP smoke. Actions
  are pinned; permissions contents:read; no checkout credential persistence,
  deployment or image publication. Actual exact-SHA CI remains required.
- Full local verification exposed Windows readonly Git fixture cleanup and four
  old non-UTC test assertions. Cleanup has a narrow readonly-file retry; assertions
  now convert instants with utc(), without changing product timestamp semantics.
  PostgreSQL targeted70tests passed/1Windows-only skip; the next full runs passed874Windows /937Linux cases but each rejected one
  old release fixture with fake .git metadata. That fixture now uses real Git and
  explicit dummy tracked inputs;27Windows packaging/lock/runner tests passed. Final
  full corrected suites and exact-SHA CI are still pending. A fresh read-only review found one Important Git fallback defect,
  now RED→GREEN, and one deferred Minor unused provider-disabling environment alias.
  Full rulings and boundaries are in the implementation plan and
  [reproduction guide](REPRODUCIBLE_CHECKS.md).
- R12 remains OPEN until final local checks and the actual pushed SHA's complete
  CI succeed. R3/R11/R13/R14 are still OPEN. Docker daemon is unavailable locally;
  no container/full-topology/provider or production assurance follows from YAML
  inspection alone. Existing untracked data, reports, releases and environments
  remain untouched and outside the scoped commit.

# Derived snapshot integrity plan

> **For agentic workers:** Use superpowers:executing-plans inline with meaningful
> RED→GREEN regressions and one fresh read-only review at the end of this batch.

**Goal:** Continue R3 with market/risk/wallet/token intelligence: source invalidation
must hide old derived values immediately and durable bounded work must publish
traceable corrected revisions. R3 also requires later radar/early-buyer/Forward/
dataset reconstruction; the full goal is not narrowed to these four tables.

**Spec:** `docs/RESEARCH_RELIABILITY.md`, previous reconstruction plan slices 2/3.
**Stack:** SQLAlchemy, PostgreSQL, Alembic, FastAPI, existing production workers.

## Design

- A BSC canonical research epoch advances transactionally on known canonical
  invalidation. Producers capture the epoch BEFORE reading facts. ORM queries
  exclude older epochs centrally, including aliases/latest subqueries; stale
  in-flight producers cannot label old reads as the new epoch.
- The four snapshot models gain epoch/revision/status/input-manifest fields and
  revision-aware unique identities. Publication uses a per-logical-snapshot DB
  lock; repeated identical inputs converge, changed inputs append a revision and
  mark the previous one superseded. No score/input payload is overwritten.
- Reader criteria apply centrally to ORM selects; direct identity-map lookups
  are audited and guarded explicitly. Audit/rebuild queries opt into history.
- Independent DexScreener observations remain external primary evidence, with
  their own manifest and original observation time, not relabelled on-chain.
- Rebuild work is repaired by bounded cyclic cursors and evaluated from durable
  jobs. It survives out-of-order IDs, retries and restarts. An independent runtime
  worker owns this work, without Redis or provider calls.
- Rebuilds preserve as_of and known parameters. Legacy missing parameters use an
  explicit versioned defaults policy. Unsupported recipe versions stay unavailable
  with a recorded reason. Missing historical external/cached risk evidence becomes
  UNKNOWN; never reuse an unversioned Token cache as historical chain truth.
- All new producers record source fact IDs/values/digests and parameters. Smart
  money uses canonical Pair/Trade JSON markers as well as flags, and cannot use
  orphaned/future PairCreated or cached Token creation to fabricate early entry.
- Market aggregate scope follows its pair identity; partial/missing USD data is
  explicit. Empty/all-orphan input remains unavailable rather than a fabricated
  market zero. Historical losers remain observed facts and are not filtered out.

## Tasks

- [x] Reproduce same-time snapshot overwrites, stale derived reads after source
  invalidation, orphan/future creation attribution and cross-pair aggregate inputs.
- [x] Add common schema/epoch/audit/rebuild state and migration 0037→0038 preserving
  historical values, quarantining pre-lineage derived data and refusing lossy downgrade.
- [x] Implement capture-before-read, immutable publication/manifests and reader
  visibility, including aliases/subqueries and explicit direct-ID access guards.
- [x] Integrate four producers and canonical selection, preserving UNKNOWN and
  negative/losing observations. Preserve external observation source identity.
- [x] Hook canonical invalidation transactionally; durable bounded repair/evaluation
  worker rebuilds old as_of snapshots, known parameters and cross-token wallet history.
- [x] Verify stale publisher, two-writer convergence, rollback, process restart,
  cursor inversion, source lineage and migration on isolated PostgreSQL.
- [x] Fresh reviewer, one material regression-driven fix pass; full backend/web,
  Ruff/build/typecheck/config checks; owned resource cleanup and local commit.
- [x] Record actual evidence and remaining R3/R6–R14 without claiming completion.

## Review focus

Identity map vs SQL reads; alias latest-row subqueries; producer capturing epoch
after facts; same-time concurrent writes; last canonical wallet/token trade removed;
cross-token wallet profiles; stale jobs when epochs advance again; legacy unknown
policy/external evidence; orphan creation cache fallback; source-free market points;
missing historical pair time; partial USD coverage; independently observed provider data.

## Ledger

Previous goal turn was progress: verified commit `cc68dc3`, 538 backend/56 PostgreSQL/
42 web cases. Revalidated tracked worktree clean there. Existing untracked product
source/data/release files are preserved. Continue under the user's autonomous goal.

Ruling: conservative chain-wide epoch invalidation prevents transitive old-wallet
scores from leaking into other token rollups while a bounded worker reconstructs
them. Selective dependency invalidation and dense history costs remain R11 work;
the DB invalidation itself must not synchronously traverse every snapshot.


## Execution and verification

- Six initial public-path regressions failed: same-time intelligence/risk updates
  overwrote old data; orphan creation/token cache and JSON markers contaminated
  inputs; cross-pair market aggregation exposed a timezone comparison; an empty
  orphan trigger still published KNOWN market data. Corrected source selection,
  pair scope and immutable revision publication now pass.
- Captured epochs, centralized ORM visibility, persisted identity-map guards and
  historical audit opt-in are integrated. Rebuild jobs/cyclic repair and independent
  worker/heartbeat are present; rollback/restart/unsupported policy are exercised.
- Ruling: all persistent snapshot result/input fields are immutable through ORM
  updates; only superseding status is allowed. Removing a manifest or reactivating
  an old status cannot bypass this. Primary provider observations retain source.
- Ruling: unversioned Token risk cache is excluded even from ordinary aggregation;
  explicit resolved evidence plus canonical trades are the inputs. Legacy risk
  liquidity serialization fixtures now provide explicit valued evidence. Missing
  historical external dimensions remain UNKNOWN rather than reusing caches.
- Ruling: production migration seeds epoch one. capture_epoch is a pure SELECT
  (zero only for unmigrated isolated fixtures), avoiding a bootstrap writer lock.
  Actual PostgreSQL reproduced the initializer lock before this correction.
- One fresh reviewer found four material issues. Each failed before the single
  fix pass: empty fallback recapturing a later epoch, a stale producer superseding
  a corrected winner, cached identity-map status accepting a superseded risk row,
  and int/Decimal type differences changing manifests after reload. All pass now.
- Alpha fixtures had trade block1 preceding their declared creation block100;
  corrected the fixture facts rather than weakening canonical-parent guards.
- Final backend **561 passed, 13 skipped, 13 existing warnings**. Isolated database
  command **65 passed**: **61 actual PostgreSQL cases**, three existing async SQLite
  foundation cases and one heartbeat simulation. Includes two writers, epoch/read
  overlap, lower-ID late commit, durable restart, history/audit and all four historical
  migration paths through current head 0038 with refused lossy downgrade.
- Web **42 passed**, TypeScript/build passed; full Ruff, whitespace, Compose config,
  Alembic head and offline SQL generation (1,193 lines through all revisions) passed.
  Owned schemas were zero; temporary SQL was removed and labelled verification
  container/anonymous volume removed after ownership validation. No real provider,
  production rewrite, deploy, merge or push occurred.
- R3 remains open for EarlyBuyer/positions, Radar/Evidence/Signal caches, Forward
  entry lineage and exported datasets/backtests. R6–R14 remain required. Coarse
  invalidation, dense manifests/history and full worker dashboard/latency acceptance
  remain R11/R14; full clean-checkout source/CI/deployment closure remains R12/R13.

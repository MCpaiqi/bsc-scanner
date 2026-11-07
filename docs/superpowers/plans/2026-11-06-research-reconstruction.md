# Research reconstruction implementation plan

> **For agentic workers:** Use superpowers:executing-plans inline, test-first,
> one fresh read-only review after the batch. Continue under the active user goal.

**Goal:** Advance the full R3 requirement: canonical invalidation must propagate
to derived research, old results stay auditable, and durable work reconstructs
trusted replacements. This plan does not redefine R3 as Alpha alone.

**Spec:** `docs/RESEARCH_RELIABILITY.md`, R3 and the existing Alpha coverage policy.
**Tech stack:** SQLAlchemy, PostgreSQL, Alembic, FastAPI, offline fake-RPC tests.

## Design and sequence

1. Alpha is the first vertical slice: its source proof and durable work already
   exist, but INVALIDATED scored rows never rebuild. Append a research revision
   with an explicit predecessor, coverage generation and immutable entry input
   manifest; retain old scores/features. Outcomes also cite their exact inputs.
2. Extend invalidation/reconstruction to market, risk, wallet/token intelligence,
   early-buyer and radar derived snapshots. Readers must exclude invalid inputs;
   source windows and semantic versions must survive rebuilding. This is required
   subsequent R3 work, not proven by finishing slice 1.
3. Carry lineage through materialized datasets/backtests and Forward observations;
   mark stale artifacts explicitly rather than rewriting published historical
   results. Verify losers survive rebuilding, including point-in-time boundaries.

**Ruling:** User has authorized continuous autonomous optimisation; keep the
existing design and make routine implementation choices without another interview.
Append-only Alpha revisions are preferable to overwriting scores (loses evidence)
or leaving invalid rows permanently stalled (does not restore operation).

## Slice 1 architecture

- Add `input_revision`, `supersedes_id`, `input_manifest` to AlphaSnapshot. Extend
  its identity by revision, retain v1/v2/current rows and link corrected revisions.
- Registration creates a replacement only if there is no usable current-generation
  job for that window. Concurrent registrars converge via the unique revision key.
- Old scored inputs remain unchanged. Canonical reorgs invalidate old revisions
  and enqueue new pending inputs; rediscovery preserves orphan history and makes
  new windows using the new creation anchor. No sliding age cutoff is introduced.
- Provenance includes exact pair creation facts, entry trades used by features/
  VWAP, receipt boundaries, policy ID, evaluation versions and stable SHA-256
  digests. Future observations are recorded separately from immutable entry inputs.
- Existing v3 scores lacking manifests become invalid history at migration; cyclic
  repair creates replacements. Old receipt generations are invalidated and runtime
  re-scans before trusting rebuilt labels; migration never makes provider calls.
- Current API/statistics exclude invalid revisions; a bounded history endpoint
  exposes their predecessor, reason, manifest and label status. Current views
  select one revision per window and distinguish pending rebuilding from absence.
- Audit the removed-log path as well as periodic reorg reconciliation. A removed
  source cannot leave its Alpha inputs/coverage trusted until the next full scan.
- Preserve explicit UNKNOWN and PAPER/SHADOW. No deploy/provider/production rewrite.

## Tasks

- [x] Reproduce invalidated scores never rebuilding, concurrent duplicate revision
  registration, same-address canonical rediscovery preserving prior inputs, and
  missing source lineage through existing public paths.
- [x] Add revision-aware schema/model and PostgreSQL 0036→0037 migration tests;
  preserve legacy values and refuse lossy downgrade after revisions exist.
- [x] Implement generation-aware registration, immutable entry manifests and
  bounded durable rebuilding. Prove partial/failed coverage stays UNKNOWN and
  old future prices cannot contaminate new entry features.
- [x] Carry exact lineage for outcomes and remove stale trusted results from API;
  expose bounded revision history with explicit validity and predecessor.
- [x] Close removed-log and reconciliation invalidation paths transactionally;
  prove rollback, stale workers and two-connection revision races on PostgreSQL.
- [x] One fresh reviewer; regression-driven material fixes, full backend/web/Ruff,
  isolated PostgreSQL/migration checks, owned resource cleanup and local commit.
- [x] Record actual evidence and outstanding slices 2/3 in reliability ledger;
  keep R3 and the overall goal open until all scope is verified.

## Review focus

Zero-log receipt reuse after missing-manifest migration; orphan PairCreated changing
timestamps; multiple revisions with the same window; late/wrong-generation worker;
duplicate invalidations; transaction lock order; API history as audit rather than
trusted performance; exact decimal/raw unit reconstruction; no unbounded migration
provider calls; v1/v2 preservation.

## Ledger

Previous goal turn was progress: commit `bbad003`, with 523 backend cases, 39 actual
PostgreSQL cases and 41 web cases verified. Revalidated current tracked worktree
clean at that commit. Existing untracked frontend/data/release files stay preserved.

Ruling: migration invalidates existing receipt generations once, instead of
reusing them. Evidence: the pre-0037 direct removed-log pipeline invalidated trades
without invalidating Alpha coverage, so a still-marked-canonical receipt may be
stale. Requiring a fresh confirmed runtime scan costs archival RPC work, but avoids
inventing complete horizon coverage from a pre-fix receipt. The original intended
reuse is superseded by this evidence; no provider calls occur during migration.


## Slice 1 execution evidence

- Existing public paths failed RED for never-rebuilt INVALIDATED scores, absent
  exact lineage, no replacement convergence and missing rediscovery windows.
  Revision-aware registration now appends replacements and retains predecessors.
- Entry/future manifests are separate; current/history DTOs use summaries and a
  full-lineage URL. Pending rebuilding has a NULL score and visible drawer state.
- The pre-fix removed path left research trusted; the failing regression now
  verifies raw/source, coverage and replacements roll back together. A real
  BSCRealtimeIngestionService.process_log test verifies committed invalidation.
- One fresh read-only reviewer reproduced two P1 lifecycle gaps: never-scanned
  orphan rediscovery, and unprojected removed evidence failing to invalidate a
  receipt. Both failed before the fix pass and pass now. Duplicate orphan notices
  converge; changed PairCreated before old removed notice also resets work.
- Decimal.normalize() rounded 38-digit source values under a 28-digit context,
  causing a reproduced digest collision. Exact formatting fixes it. Database
  reload digest checks and distinct last-digit tests pass.
- Initial full suite exposed a legacy removed-record fixture without block_number;
  inference from exact matching canonical projections now preserves that contract.
- Final backend **538 passed, 9 skipped, 13 existing warnings**. Actual isolated
  PostgreSQL 16 **56 passed**, including two concurrent revision writers, publication
  serialization, migration 0036→0037 preserving scores/requiring fresh coverage,
  and refused lossy downgrade. Web **42 passed**, typecheck/build passed. Full Ruff,
  whitespace, Compose config and Alembic head 0037 passed.
- Owned schemas were zero after tests. The labelled verification container and its
  anonymous volume were removed after ownership validation. No provider calls,
  production database operations, deploy, merge or push occurred.
- Ruling: capture the pre-existing web/src/types/radar.ts product file deliberately
  because the nullable Alpha detail contract changed; all its existing declarations
  are preserved. Other untracked source/data/release files remain untouched; full
  clean-checkout closure remains R12.
- Slice 1 is complete locally. Slices 2/3 above remain required R3 work and the
  overall goal remains active; this does not establish their validity or R14.

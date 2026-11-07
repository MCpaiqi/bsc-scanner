# Forward Evaluation Integrity Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans in this session,
> with test-first fixes and one fresh read-only review after implementation.

**Goal:** Satisfy R5 of the persistent research desk objective and remove the
all-history scan from Forward evaluation work. R3/R4/R6–R14 remain active.

**Spec:** `docs/RESEARCH_RELIABILITY.md`.

**Architecture:** A pure price-path evaluation module defines endpoint selection
and peak drawdown for both backtest and Forward. Forward outcomes are versioned;
existing results retain legacy version. Pending work is seeded and evaluated in
bounded batches with persistent retry times. New observations identify their USD
entry source, and future reads use explicit USD facts with canonical provenance.

**Tech stack:** Python 3.12+, SQLAlchemy, Alembic, PostgreSQL/SQLite, pytest.

## Global constraints

- Preserve BSC, PAPER/SHADOW, UTC, provenance, explicit UNKNOWN, and old results.
- No live-provider calls, production data rewrites, merges, or pushes.
- Current policy: 5-second symmetric endpoint tolerance; nearest valid endpoint,
  earlier timestamp wins an equal-distance tie. No point newer than evaluation at.
- Price must be finite and strictly positive. Path includes entry at T0, ends at
  the selected endpoint; drawdown is price / running_peak - 1. Stop labels measure
  loss against entry, separately from reported peak drawdown.
- Current algorithm version is `forward-v2`; tolerance and endpoint/source metadata
  are persisted. Changing this policy requires another evaluation version.
- Sampled extrema describe the observed price path; they are not evidence of
  uninterrupted chain coverage. Alpha coverage proof remains a separate R4 gate.

## Review focus

- A 30s result cannot accept a price two hours later, including after worker restart.
- Both initial losses and peak-to-trough losses must be captured, including invalid
  numeric inputs, early/late endpoints, tie ordering and the evaluation cutoff.
- Orphan/removed facts cannot be endpoints or extrema; USD and quote prices must
  not mix. Missing/ambiguous entry units remain unavailable.
- Legacy COMPLETE outcomes coexist with current version and do not enter current
  statistics. Version changes must not silently overwrite prior research results.
- Missing-price tasks cannot block later due tasks indefinitely; processed amount
  is bounded, retry times persist, and downtime can be caught up.

## Task 1: Pure shared path evaluation

Files: `src/smms/evaluation.py`, `src/smms/backtest/signal_engine.py`,
`tests/unit/test_forward_evaluation_integrity.py`.

Interface: immutable `PricePoint`, `PathEvaluation`, and `evaluate_price_path`
returning an unavailable result when a valid bounded endpoint is absent. Existing
`evaluate_signal` and `Evaluation` public interfaces remain available.

- [x] Add public-path regressions for late endpoints, true/initial drawdown,
  canonical exclusions, finite prices and offline/live equality; run RED.
- [x] Implement shared pure algorithm and adapt backtest without future leakage.
- [x] Run new regressions plus current backtest/dataset suites, verify GREEN.

## Task 2: Versioned durable Forward outcomes

Files: `src/smms/db/models/live.py`, new migration after `20260919_0034`,
`src/smms/radar/live_forward.py`, regression tests.

Interface: outcome identity `(observation_id, horizon, evaluation_version)`;
store endpoint time, signed endpoint offset, tolerance, price/path provenance and
`next_evaluation_at`. Old rows remain `legacy-v1`; new writes use current version.

- [x] Add coexistence/current-statistics and retry/restart regressions; run RED.
- [x] Add migration/model columns and index, preserve legacy data.
- [x] Bound initialization/evaluation batches; batch-fetch prices per token and
  retain errors/retries as pending. Current stats exclude legacy outcomes.
- [x] Verify migrated PostgreSQL persistence and SQLite test adapter behavior.

## Task 3: Explicit USD inputs and provenance

Files: `src/smms/radar/live_forward.py`, shared canonical fact predicates as needed,
`tests/unit/test_forward_evaluation_integrity.py`.

Interface: immutable T0 observation captures USD source id/pair/time; price paths
accept explicit USD TradeEvent/TokenMarketSnapshot evidence, plus explicitly marked
USD MarketEvent legacy records. Missing price basis is not silently inferred.

- [x] Add snapshot-only production path, quote-unit rejection, stale/future/orphan
  price and endpoint metadata tests; run RED.
- [x] Resolve entry facts as-of T0 and future facts under pair/canonical/cutoff
  rules. Preserve observed-source distinctions in results.
- [x] Run targeted suites, full backend, Ruff, web tests/typecheck/build.

## Task 4: Review, migration and evidence

- [x] Fresh reviewer checks the diff against the above failure modes.
- [x] Reproduce material findings before correction; complete one fix pass.
- [x] Verify isolated PostgreSQL migrations from old head preserve legacy rows
  and allow current results alongside them; clean up owned test resources.
- [x] Document policy, rebuild/version behavior and remaining R4 work, record
  exact verification evidence, then commit only this batch's owned files.

## Ledger

Previous goal turn: progress, verified commit `2093841`; current worktree matches
that commit with the pre-existing untracked frontend/research artifacts preserved.
Ruling: execute Forward before durable Alpha coverage because Forward defects and
their price-source dependency are fully localized; the full objective is unchanged.
Ruling: explicit unit provenance is required for corrected results, even where old
generic prices previously produced a number. Legacy results remain inspectable.

Fresh review found ID/commit-order inversion and finite-return storage overflow.
Both were reproduced against actual PostgreSQL before correction. Ruling: enqueue
new production observations' outcomes atomically; keep a cyclic, bounded legacy
repair cursor so a late lower-ID commit cannot be permanently skipped. Ruling:
use PostgreSQL unconstrained NUMERIC for return metrics, preserving valid values
instead of clipping them or inventing a negative/unavailable outcome. Persist
decimal calculation strings with source/path provenance; keep legacy rows intact
and refuse downgrade while corrected results would lose policy metadata/precision.
Additional public-path checks enforce performance as-of cutoff and expose the
shared recipe in backtest summaries. No Alpha coverage completion is claimed.

Final evidence: backend 487 passed / 4 skipped / 13 existing warnings; PostgreSQL-specialized command 37 passed (30 Forward DB cases, 6 pure cases, 1 actual historical migration case); web 40 passed; typecheck/build/Ruff/whitespace passed. Fresh review identified two persistence defects; each was reproduced RED on PostgreSQL and fixed in one review fix pass. No additional actionable minors. R5 is locally verified; R3/R4/R6–R14 remain open in the programme ledger. No live providers, production rewrites, merge or push.

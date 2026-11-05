# Foundation Integrity Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan
> task-by-task. Steps use checkbox syntax for tracking. The active user goal
> authorizes continuous execution; preserve current local source and data.

**Goal:** Restore concurrent ingestion progress and trustworthy canonical/risk
inputs for the broader BSC research desk goal.

**Architecture:** Keep existing worker and research modules. The reconciler owns
one lock acquisition. Dataset exports use `overall_risk`, retaining legacy `level`
read compatibility. Canonical/removed predicates apply at actual fact queries.

**Tech Stack:** Python 3.12+, FastAPI, SQLAlchemy sync/async, pytest, PostgreSQL;
SQLite is an offline regression adapter, not a replacement for deployment checks.

**Spec:** `docs/RESEARCH_RELIABILITY.md`, requirements R1–R3. R4–R14 remain active.

## Global Constraints

- BSC only; PAPER/SHADOW; live trading remains disabled.
- Preserve UTC, provenance, point-in-time cutoff and explicit UNKNOWN semantics.
- No live ingestion, production data updates, destructive cleanup or secret reads.
- Preserve existing public entry points and previously materialized dataset reads.
- Only stage files this batch owns; no unrelated untracked artifacts.

## Review Focus

- A running background reconciliation must invalidate an actual orphan and then
  allow subsequent ingestion, rather than only passing a direct method call.
- Cancellation while blocked or holding the lock must leave it usable.
- CRITICAL must remain HIGH_RISK after serialization/materialization; legacy level
  payloads must continue to work, without weakening point-in-time checks.
- `canonical=False, removed=False` and `canonical=True, removed=True` must each be
  excluded; a control fact must survive and a genuine losing token stay eligible.
- Removing orphan facts must exclude both feature and future evaluation prices,
  and must change risk evidence through the real aggregator.

## Task 1: Reconciler lock ownership

**Files:** `src/smms/ingestion/bsc_stream.py`,
`tests/unit/test_research_integrity.py`.

**Interface:** Preserve `reconcile_reorg_window(latest, lookback)` as the externally
locked entry. The already-locked background loop uses its existing unlocked
implementation. Test real AsyncSession and stored Pair invalidation.

- [x] Add a regression starting `_run_reorg_reconciler`, wait for a real orphan
  Pair to become removed, then process a real ChainLog; ensure cancellation cleanup.
- [x] Run targeted test; expect timeout before the fix.
- [x] Remove nested lock acquisition in background loop.
- [x] Run the new regression and existing reorg/stream tests; expect pass.

## Task 2: Risk dataset contract

**Files:** `src/smms/backtest/dataset.py`,
`src/smms/backtest/signal_engine.py`, `tests/unit/test_research_integrity.py`.

**Interface:** Export canonical `risk.overall_risk` alongside the existing `level`
alias; consume `overall_risk`, `risk_level`, then legacy `level`. Missing evidence
stays UNKNOWN. All other row payload fields retain their current shape.

- [x] Add literal LOW/CRITICAL/UNKNOWN builder→JSON→backtest assertions and a
  materialized CRITICAL roundtrip; legacy level-only CRITICAL remains HIGH_RISK.
- [x] Run tests; expect CRITICAL→UNKNOWN and absent canonical field failures.
- [x] Normalize the producer and legacy consumer without changing scoring weights.
- [x] Run dataset/signal suites; expect pass including future-snapshot safeguards.

## Task 3: Canonical historical and risk facts

**Files:** `src/smms/backtest/dataset.py`, `src/smms/risk/aggregation.py`,
`src/smms/db/facts.py`, `tests/unit/test_research_integrity.py`.

**Interface:** No new public interface. Pair/TradeEvent require canonical=True and
removed=False; lifecycle and MarketEvent use their model's actual reorg markers.
Keep event-time cutoffs, real losing assets and provenance unchanged.

- [x] Add independent orphan/removed flag cases for universe and price paths,
  including future paths; assert retained valid control facts and real risk output.
- [x] Run tests and confirm invalid facts are currently included.
- [x] Apply actual fact validity predicates at lifecycle/dataset/risk queries.
- [x] Run focused research/aggregation suites, then full backend, Ruff and web
  test/typecheck/build checks; record results and limitations.

## Task 4: Review and evidence

- [x] Fresh reviewer checks the batch diff against R1–R3 and Review Focus.
- [x] Resolve material findings with failing tests before changes.
- [x] Record fresh command results and exact remaining R1–R14 work in the ledger.
- [x] Commit only verified batch files to the local codex branch; do not merge/push.

## Execution ledger

Pre-flight: Tasks 2 and 3 share dataset.py; task 3 preserves task 2's risk payload.
Task 1 is independent. The full goal remains active beyond this batch.

Ruling: use one SQL predicate module for the four actual fact models, including
JSON reorg markers. Bump default builder identity to dataset-v3 so rebuilt rows
and config hashes distinguish the corrected extraction. No existing database
artifacts are automatically rewritten; old materializations need explicit rebuild.

Review fix pass: the unlocked call exposed further shared AsyncSession ownership
gaps. Own the complete live log transaction and status reads under the DB lock;
probe head outside it; drain an entered reconciliation transaction before task
cancellation and checkpoint shutdown. Replace the handoff test's helper mocks
with assertions on actual raw persistence and the stopped checkpoint. Also stop
cached Token launchpad fields resurrecting orphan CREATED evidence; preserve
legacy fallback where no creation evidence was observable at the discovery time.

Foundation batch evidence: 452 backend tests passed, 2 skipped; 40 web tests passed, typecheck/build passed; Ruff and whitespace checks passed. PostgreSQL 16: revisions 0001–0034 migrated successfully; 32 sync research regressions passed on generated isolated schemas, alongside 3 SQLite async regressions. Fresh review requested three fixes; each was reproduced and corrected in the single review fix pass. Temporary verification container and its anonymous volume were removed. The broad goal and R3–R14 remain active.

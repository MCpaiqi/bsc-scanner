# Historical dataset integrity

Continue inline on codex/research-integrity. Preserve untracked product/data files.
Use meaningful RED→GREEN tests, the existing shared research publication contract,
and one fresh read-only reviewer after implementation. R1–R14 remain the programme.

## Design

Dataset-v5 uses the Forward USD source adapter and bounded entry selector. Freeze
the entry pair for outcome prices and retain the five-second endpoint tolerance.
Never interpret unlabelled quotes as USD or consume unversioned TokenScore caches.
Missing prices remain UNKNOWN without excluding losing tokens from the universe.
Export and preserve evaluation_as_of so delayed provider receipts are compared at
the same availability time in historical and live public paths.

Materialized datasets become immutable, epoch-bound research revisions with source
manifests and reverse dependency edges. Rows inherit parent visibility and are
immutable. Old IDs remain auditable but cannot serve trusted backtests; stable
dataset labels resolve current revisions. Durable reconstruction preserves dates
and recorded recipes, waits for prerequisites and does not commit caller work.
Legacy artifacts are quarantined; missing recipes use an explicitly named upgrade
policy rather than claiming their original extraction was reproduced.

## Tasks

- [x] Reproduce quote/USD, mixed-pair, endpoint, stale-cache and stale-artifact bugs.
- [x] Share entry selection, implement dataset-v5 and exact source manifests.
- [x] Add dataset revision schema, migration, visibility, API guards and repair.
- [x] Verify restart/rebuild, legacy migration and transactions on isolated PG.
- [x] Full appropriate checks, one read-only review, ledger and scoped commit.

Forward entry/outcome lineage, risk field compatibility and R6–R14 remain separate
required work. This slice alone does not complete R3 or the programme.

## Evidence

Final backend650 passed/22 skipped/13 existing warnings; initial database121 passed,
then final PostgreSQL24 passed after the one review/fix pass. Late receipt and JSON
cache defects both received regressions. Full Ruff(src/tests), whitespace, Compose,
Alembic0041 head and offline SQL1295 lines passed. Test-owned schemas were verified
zero before removing the labelled container/volume. Automatic approval rejected
the combined recursive temporary-environment cleanup with only "blocked by policy";
the generated .venv, uv.lock and temporary offline SQL remain outside the commit.
Preserve pre-existing untracked product/data files. Local commit only; no deploy,
push, production migration or live provider calls. R3/R6/R7/R9–R14 remain active work.

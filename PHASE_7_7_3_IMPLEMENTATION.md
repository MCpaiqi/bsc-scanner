# Phase 7.7.3 — Fusion Readiness Hardening

## Scope

Completed on the Phase 7.7.2 baseline. No RadarCandidate, Audit Queue, GMGN,
GoPlus, new frontend or Phase 7.8 work is included. Live trading remains disabled
by default (`ENABLE_LIVE_TRADING=false`) and is explicitly disabled by the test runner.

## Historical universe

HistoricalDatasetBuilder now derives market lifecycle timelines from existing canonical
facts. Launchpad creation/discovery or a timestamped launchpad trade establishes
observability without a DEX pair. DEX-only tokens become eligible at timestamped pair
creation, including tokens whose creation predates their pair. Unknown pair timestamps
cannot establish eligibility. Explicit migration events establish MIGRATED_TO_DEX;
pair creation alone establishes DEX_PAIR_DISCOVERED, not migration.

Each row includes discovery_timestamp, universe_reason and market_lifecycle.
Only timeline facts at or before the row timestamp determine its state. Lifecycle
transitions are row timestamps even if no price observation exists. Current outcome/status
is not used. Missing price/market/risk history continues to be reported as missing data.

Observability means public chain event time. It does not reconstruct local ingestion delay;
the schema has no bitemporal ingestion ledger. Token.launchpad is creation-source metadata,
and first_seen_at is a fallback when launchpad creation_timestamp is unavailable.

## Feature / evaluation boundary

Dataset model default is dataset-v2. New exports have feature_price_history (<= as_of)
and evaluation_price_path (> as_of, bounded to the row's 24-hour evaluation horizon),
without mixed prices / future_prices aliases. run_backtest constructs FeaturePriceHistory
before signal generation. It validates timestamps, copies mapping inputs into read-only
mappings, and generate_signal filters again against its own timestamp. The evaluator alone
receives the evaluation path. The legacy prices argument remains filtered for compatibility.
ISO timestamps in materialized JSON rows are accepted.

Existing dataset files can still be read, but must be rebuilt to correct their old universe
membership; price compatibility cannot recover previously excluded launchpad rows.
No database migration is required. The existing Phase 7.7.2 assertion for a future price
was updated to inspect evaluation_price_path; its original intent remains covered.

## Release audit

Run `python scripts/release_audit.py <release-directory>` before distribution.
It recursively checks environment files, key files, private-key markers, credential
assignments/URLs/provider token patterns, Git metadata, Python/tool caches, phase and
pytest temporary directories, database dumps, backups, archives and unreadable paths.
Symlinks and uninspected archives fail closed. Only path / reason / severity are printed;
matched contents are never printed. Any finding exits 1; a clean tree exits 0.

The credential scan is deliberately conservative and may flag source/test placeholders
or type annotations. It cannot prove absence of encoded or arbitrary unnamed secrets.
Review findings in the local files and prepare a clean release tree; there is no blanket
ignore switch for credentials. `.env.example` is exempt only from the filename rule,
not credential scanning.

Running the audit against this working repository returned 1 as expected, detecting the
local .env, .git, caches and pre-existing temporary artifacts, plus possible credential
findings. No secret values were emitted and no local files were removed by the audit.
This repository working directory is not certified as a clean release bundle.

## Standard test entry

Use `./scripts/test.ps1`, `bash scripts/test.sh`, or `python scripts/test.py`.
The shared runner installs editable dev dependencies, runs pytest, then ruff check.
It removes inherited PYTHONPATH/PYTEST_ADDOPTS, fixes PYTHONHASHSEED=0 and TZ=UTC,
loads pytest-asyncio explicitly, disables arbitrary plugin auto-loading, and creates a
unique temporary directory per run. It verifies containment before cleaning its own
scratch directory. Existing developer temporary directories are not touched. This also
avoids the stale Windows pytest/cache directory permissions observed during implementation.
Use a virtual environment; dependency ranges remain those in pyproject.toml, not a lockfile.

## Validation

Windows / Python 3.13.7: `./scripts/test.ps1 -q` completed editable installation,
**159 tests passed**, and **ruff check passed**. Five existing dependency deprecation
warnings remain (Starlette/httpx and Alembic path_separator). The POSIX wrapper shares
the verified Python runner but was not executed on a POSIX host in this session.
`git diff --check` passed.

Regression coverage includes launchpad-before-pair eligibility, DEX eligibility starting
at pair observation, unknown pair time rejection, future lifecycle isolation, feature
price validation/immutability, future price changes affecting outcomes but not signals,
materialized-format price separation, unsafe artifacts, private-key/credential formats,
redacted CLI output, and clean/unsafe CLI exit codes.

Phase 7.7.3 ends here.

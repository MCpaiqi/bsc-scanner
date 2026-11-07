# Canonical research readers implementation plan

> **For agentic workers:** Use superpowers:executing-plans inline, with one fresh read-only reviewer after verification.

**Goal:** Prevent orphaned facts, mixed epochs and missing USD values from producing apparently trustworthy legacy wallet research results.

**Architecture:** A shared synchronous reader runs inside each API's existing AsyncSession.run_sync boundary. It freezes one UTC cutoff and epoch, applies canonical SQL before limits and neighborhood discovery, records exact inputs in an inline manifest, and checks the epoch and snapshot dependencies before returning. Existing response fields remain, with nullable unknown monetary results and additive provenance.

**Tech stack:** Python, SQLAlchemy, FastAPI, pytest, SQLite and isolated PostgreSQL.

**Spec:** docs/RESEARCH_RELIABILITY.md, R3 and the existing source integrity acceptance criteria.

## Constraints

- Preserve the original R1–R14 scope; this is one R3 slice.
- No live providers, production database, deployment or publication.
- Preserve user cohorts, including empty results; label supplied outcome claims UNVERIFIED.
- Inline manifests are response evidence, not a durable stored research revision.
- Keep graph and replay limits explicit; dense-history optimization remains R11.

## Review focus

- Orphan/future parent pairs and JSON reorg markers must be excluded before limits and token discovery.
- Cached ORM values must not override persisted values.
- Epoch changes or superseded wallet dependencies during calculation must return 409 REBUILDING.
- Missing, negative or nonfinite USD values must remain unknown; observed zero must survive.
- One cutoff, deterministic ordering and exact input manifests must make responses auditable.

## Task: Shared reader and legacy research APIs

- [x] Add failing tests in tests/unit/test_canonical_research_readers.py and tests/unit/test_phase8_wallet_intelligence.py for the review focus above.
- [x] Confirm failures arise from the unsafe readers and zero coercion.
- [x] Create src/smms/research/reads.py with CanonicalResearchRead, bounded canonical reads, snapshot tracking and final validation.
- [x] Update src/smms/api/app.py wallet profile/graph/clusters/replay, token flow and BSC market events to use the shared boundary; remove unused unsafe trade helpers.
- [x] Preserve USD unknowns in src/smms/smart_money/intelligence.py and expose valuation/classification coverage.
- [x] Verify targeted tests on SQLite and isolated PostgreSQL, full backend tests, Ruff and whitespace. Final backend: 713 passed, 30 skipped; isolated PostgreSQL: 69 passed.
- [x] Request one fresh read-only review, resolve actionable findings, update the reliability ledger and commit only this slice's files.

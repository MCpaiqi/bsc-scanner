# Transactional Radar effects implementation plan

> **For agentic workers:** Use superpowers:executing-plans inline; request one fresh read-only review at the end.

**Goal:** Normal/deferred projections retain all canonical evidence and produce the same final-state audit, baseline and rule effects; rolled-back work never reaches subscribers and committed work survives relay failure/restart.

**Spec:** docs/RESEARCH_RELIABILITY.md R6; R7 replay remains a separate requirement.

**Architecture:** Share the projection-effect function, attach every eligible input's proof but refresh each deferred candidate once, and insert immutable event envelopes in the owning transaction. A separately supervised event-relay role claims bounded batches with expiring fenced leases, commits claims before networking, and acknowledges stable event IDs in fresh transactions. Delivery is at least once, not exactly once; consumers must tolerate duplicate IDs and refetch current canonical state.

**Approaches:** After-commit callbacks prevent rollback ghosts but lose events on process failure. Redis-only queues are not atomic with canonical DB state. The chosen PostgreSQL outbox preserves both atomicity and restart recovery and uses existing Redis/local SSE fanout for transport.

**Ruling:** The existing R6 acceptance scope and user's instruction to continue implementation authorize this implementation choice and subsequent verified GitHub pushes. No additional design approval is needed. Deferred processing coalesces intermediate refreshes, while retaining all source proofs and applying all final-state effects.

**Tech stack:** SQLAlchemy, Alembic0044, FastAPI, asyncio, Redis, pytest, SQLite and isolated PostgreSQL.

## Review focus

- Outer rollback and failed audit work must not leave sendable event rows or local/Redis messages.
- All early meaningful evidence must survive a later event in the same deferred chunk.
- Claim locks must be released before networking; multiple workers, expired leases and stale acknowledgements must be fenced.
- Retry after local success/Redis failure must preserve event ID and original payload/time, without losing committed events on cancellation/restart.
- Deferred processing, manual alert evaluation, audit lifecycle and audit feature events must all use the durable boundary; no production caller may retain fire-and-forget publication.

## Tasks

- [x] Write RED regressions for rollback ghosts and normal/deferred audit/baseline/alert/evidence effects in tests/unit/test_transactional_radar_effects.py.
- [x] Add RadarEventOutbox model/export and Alembic0044 migration; protect immutable envelope fields and reject lossy downgrade.
- [x] Implement src/smms/radar/outbox.py enqueue, bounded lease claims, fenced completion/retry and async relay_once with detached immutable payloads; test dedup, rollback, retry/restart, cancellation, lease expiry and PostgreSQL concurrent claims.
- [x] Refactor hooks and engine to share final-state effects, attach all deferred proofs, refresh each affected candidate once, freeze effect payloads and enqueue only.
- [x] Enqueue audit-start in the claim transaction; roll back failed audit computation before recording failure; enqueue completion/failure and feature/alert events with their owning mutations. Move manual rule delivery to the same boundary.

Ruling: The worker commits its claim/start before doing audit work and owns that work transaction. Roll back that complete work transaction on failure, then persist the failed attempt. A nested savepoint adds no ownership benefit here and risks retaining unrelated partial mutations; rollback also recovers a session after a database flush failure.
- [x] Add supervised event-relay runtime/Compose service, heartbeat and paged read-only delivery audit API. Verify runtime wiring and bounded failure handling.
- [x] Run full backend/Ruff/whitespace, isolated PostgreSQL, migration upgrade/downgrade safety and Compose configuration. Request one reviewer, fix reproduced findings, update reliability ledger, scoped commit and push. Final backend: 732 passed/33 skipped; PostgreSQL/Redis: 69 passed.

# Rebuild contention and durable health implementation plan

> **For agentic workers:** Use superpowers:executing-plans inline with TDD and one fresh read-only review at the end.

**Goal:** Advance R11 by allowing independent derived reconstruction jobs to progress during repair/publication contention, with committed cross-process timing and outcomes.

**Spec:** docs/RESEARCH_RELIABILITY.md, original BSC research desk goal, especially retained history and R11. Existing user authorization covers autonomous implementation and scoped verified pushes. R3/R11–R14 remain OPEN beyond this batch.

**Architecture:** Keep existing source epochs, immutable manifests/revisions and skip-locked durable jobs. Add schema/kind-scoped nonblocking repair ownership and cursor access. Inside each job SAVEPOINT, temporarily bound PostgreSQL lock waits at250ms (or the existing shorter timeout), probe its root publication identity before reading expensive inputs, release that probe through an inner SAVEPOINT rollback, and restore the caller setting on success; SAVEPOINT rollback restores it on failure. Final publication reacquires its identity and checks the current winner. Retry a contended job without rolling back independent work. Collect repair/root-probe/rebuild timings and status counts, then publish counters through a rebuild-worker heartbeat only after the source transaction commits.

**Alternatives/ruling:** Serializing through the present repair cursors defeats independent job claims. Adding advisory publication locks to every live producer changes the whole producer protocol and still must handle old/direct row locks. Chosen: bounded database-local lock waits for reconstruction only; regular live publication keeps its protocol. Existing autonomous continuation opts out of another design approval round. No data truncation, TTL deletion, provider calls, deployment or production writes.

**Files/interfaces:**
- research/maintenance.py: reuse try_repair_ownership; add bounded_lock_wait(session, milliseconds=250), valid inside a caller-owned SAVEPOINT. SQLite is a no-op. PostgreSQL restores the previous lock_timeout exactly, preserving a shorter nonzero setting.
- research/rebuild.py: nonblocking _repair; rebuild_due(..., report=None) retains its integer return and optional report receives repair_seconds, publication_lock_seconds, rebuild_seconds and jobs_examined/completed/deferred/unsupported/stale/superseded. Probe lock_identity inside an inner rolled-back savepoint, releasing the temporary lock before reading facts. Keep retries durable and preserve last_error exception categories.
- runtime.py: run_rebuild_cycle(settings, at=None) measures REBUILD/COMMIT/IDLE, persists counts only on successful commit, propagates cancellation, handles retryable DB/I/O errors, and pulses elapsed progress during async waits. Existing forward cycle behavior remains compatible.
- research/telemetry.py + api/app.py: a separate explicit whitelist for research-rebuild heartbeats, safe malformed input handling and cross-session /health exposure. No arbitrary heartbeat data exposure.
- tests/unit/test_rebuild_runtime_budget.py: actual PostgreSQL independent sessions and normal SQLite semantic regressions.
- tests/unit/test_rebuild_runtime_health.py: committed timing, failure/cancellation, actual cycle/health and pulse shutdown regressions.
- docs/DERIVED_RESEARCH.md, docs/api.md and reliability ledger: state the concrete contract and remaining scope.

## Constraints and review focus

- Keep PAPER/SHADOW, canonical filtering, UTC cutoffs, UNKNOWN semantics and full source manifests/history. Neither a due-job limit nor a lock deadline authorizes dropping facts or marking unavailable data negative.
- Warm and cold repair, contended publication identities and dependency/write locks must not stall unrelated due jobs; savepoint rollback must leave the outer transaction usable.
- Restore existing nonzero shorter lock_timeout and zero/unlimited configuration after success/failure. A lock timeout is not a total CPU/statement/wall-time SLO.
- Retain cyclic cursor wrap for commit-order inversion, current epoch checks, unsupported-version states and one logical publication revision under concurrent writers.
- Heartbeat pulses must stop before terminal reporting; cancellation must release DB locks and not publish rolled-back counters. Malformed metrics and private errors never escape through /health.
- This batch does not complete canonical anchor/provenance retention, dense history storage or all asynchronous rebuild throughput acceptance. Those original R11 obligations remain tracked, along with R3/R12–R14.

## Tasks

### Task1: Independent reconstruction under contention

- [x] Add RED PostgreSQL warm/cold repair and root-publication contention cases with bounded lock/statement timeouts; add SQLite full-history/UNKNOWN and report semantics.
- [x] Implement bounded_lock_wait, nonblocking repair, pre-acquired identity and per-cycle report. Verify contended tasks stay PENDING with retry timestamps, independent tasks COMPLETE, manifests/history retained, and lock configuration restored.
- [x] Run new cases plus existing derived/Forward reconstruction, late-commit and concurrent publication tests; inspect all failures.

### Task2: Durable rebuild cycle health

- [x] Add RED commit/rollback/cancellation/pulse and independent health reader cases, including malformed values and a real reconstruction cycle.
- [x] Implement run_rebuild_cycle, heartbeat profile/whitelist and API source. Commit counts only after commit and stop pulse before terminal reports.
- [x] Run new health and existing forward-worker health/API source tests.

### Task3: Verification and delivery

- [x] Full backend, Ruff/whitespace and real PostgreSQL concurrency/rollback/reconstruction checks; inspect dense history behavior and record synthetic-only throughput evidence.
- [x] One fresh read-only review; reproduce Critical/Important findings RED and fix in one pass; record rulings and any remaining minors.
- [x] Update ledger/docs, verify no owned schemas/clients and stop only the owned PostgreSQL foreground process; prepare the scoped verified commit/push.

## Pre-flight and rulings

Task1 report fields are consumed by Task2 only after commit. Timings can be observed before commit; counts remain null until committed. Task2 pulse owns no source transaction and must be joined before final report. Task3 preserves the complete original scope; passing the batch does not check R11 complete.

Windows execution ruling: this repository has no existing .superpowers/sdd workspace and the skill helper scripts target POSIX. Keep this plan's progress/evidence here and in its own ignored .superpowers/sdd/2026-11-09-rebuild-contention-and-health directory, without touching another plan's artifacts. Existing codex/research-integrity branch is the verified isolated feature workspace; do not switch or clean the shared user workspace.

Task1:11 initial RED cases reproduced real cursor lock timeouts, root facts read before a failed publication and missing budget/report interfaces.11 went GREEN after implementation; an additional real dependency lock case verifies SQLSTATE55P03 rather than statement-timeout57014 and rolls back only that job. One dependency fixture omitted required risk fields; it now uses a complete market dependency fixture. This was fixture setup, not a product relaxation.

Task2:11 initial RED cases reproduced missing cycle/health interfaces;23 new+existing health cases went GREEN. Added real reconstruction and cancellation checks: cancellation interrupts a live PostgreSQL wait while publication/job/cursor locks are held, leaves attempt counts uncommitted, then a new cycle completes the same durable jobs.

Verification so far: PostgreSQL85 passed (new budgets/health, derived/Forward reconstruction and existing forward health), SQLite37 passed/15 skipped in the new+derived selection; Ruff passed. One existing derived API assertion relabelled a non-UTC PostgreSQL datetime with replace(tzinfo=UTC); it now converts via utc(). No product timestamp behavior changed.

Final review: one fresh read-only reviewer found no Critical/Important/Minor issues.
The parent identified an additional Important performance regression before delivery:
holding the pre-acquired root lock through fact calculation stalled a fresh live
publication of the same identity. A real independent PostgreSQL writer reproduced
RED. The fix probes in an inner SAVEPOINT and rolls it back before reading, then
uses the existing final-publication winner check. This preserves early contention
detection without lengthening the read phase's lock hold. The concurrent regression
also requires the new live policy/value to win rather than be replaced by the rebuild.
The actual cancellation test now deliberately reacquires a publication lock in its
recipe before interruption, rather than relying on the released probe.

Final: Ruling: full canonical-anchor/provenance retention, dense storage and production
throughput remain OPEN R11 — this batch retains data but does not certify storage
growth or a production throughput SLO.
Final: Ruling: canonical-fence latency and whole SQL/CPU/batch bounds remain OPEN —
the250ms deadline is per reconstruction lock wait; a lock-free expensive query can
still be slow, and final publication retains the existing transaction protocol.
Final: Ruling: production migration/live-provider/full topology checks remain R12–R14 —
the batch has no schema migration, deployment or provider operation.
Final: Ruling: repair ownership assumes cooperative current workers; an old cold
initializer bypassing the guard or administrative DDL can still cause waits — mixed
binary rollout and online migration coordination remain R12/R14 validation work.
Final: Ruling: cancellation during the outer5s idle sleep retains pre-existing last
heartbeat semantics rather than immediately reporting STOPPED — in-cycle cancellation
is verified; idle shutdown presentation remains part of R14 health lifecycle work.
Final: Ruling: latest reports belong to the current process instance — durable recent
diagnostics are provided, not a cross-restart time-series archive.
No deferred Minor findings. Do not dispatch a second review; complete the fix-pass
verification, then scoped commit and push under the existing user authorization.

Final verification:831 backend passed/55 skipped/13 existing warnings;86 real
PostgreSQL16 checks passed after the released-probe fix; Ruff/whitespace passed.
The parent Important finding is GREEN in
test_rebuild_read_does_not_hold_up_a_fresh_live_publication, including retention of
the new live policy/value. Real cancellation still holds an actual publication
lock at interruption. Synthetic2000/10000-fact rebuild+commit took0.539/1.900s,
retained all canonical facts and frozen old digest/UNKNOWN semantics, and kept2
history revisions after5 no-op cycles. No production SLO or storage policy is
certified. Verified zero owned schemas/other clients; foreground PostgreSQL stopped
and its process exited successfully. No frontend source, provider, production DB,
deployment or merge operation occurred. Delivery remains the authorized scoped
commit/push on codex/research-integrity; the original goal stays active.

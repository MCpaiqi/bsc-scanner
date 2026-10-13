# Phase 7.8.2 - Priority Audit Queue

## Delivered

The radar now has a durable slow-lane audit pipeline:

```text
Fast Lane -> RadarCandidate -> PostgreSQL RadarAuditJob -> Slow Intelligence Lane
```

`RadarAuditJob` persists token/chain, reason, priority, lifecycle timestamps,
attempt limits, worker ownership, and the last classified error. The Alembic
revision `20260912_0016` creates the table and claim indexes. `Base.metadata`
also includes the model for local SQLite tests.

`AuditQueue.claim()` orders by priority and uses `FOR UPDATE SKIP LOCKED` on
PostgreSQL. This prevents duplicate claims across workers; crashed RUNNING jobs
are recovered after a lease timeout. Redis is not used as queue storage.

Scheduling coalesces active jobs for the same candidate, raises the retained
priority, and records the combined reason. This provides deduplication and
flood protection while allowing urgent smart-money/risk changes to run now.

Retries classify transient, data-unavailable, rate-limit, and internal failures,
with exponential backoff bounded by `max_attempts`. Permanent errors and
exhausted retries become `FAILED` and never loop indefinitely.

The worker refreshes the existing Smart Money and Risk Engine snapshots, then
recomputes the candidate projection through `RadarEngine`. Queue metrics are
included in `GET /health` (depth, oldest age, running, success/retry/failure
counts, and average processing latency).

## CLI

```text
smms radar queue
smms radar worker [--worker-id ID]
smms radar audit TOKEN [--priority N] [--reason TEXT]
```

## Verification

`tests/unit/test_phase7_8_2_audit_queue.py` covers priority ordering, coalescing,
bounded retry, permanent failure, worker crash recovery, and successful worker
processing. The focused suite passes (`3 passed`); compileall and Ruff pass for
all changed modules.


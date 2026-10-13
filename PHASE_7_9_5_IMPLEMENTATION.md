# Phase 7.9.5 Implementation

## Validation architecture

Phase 7.9.5 adds a read-only validation lane. `scripts/phase795_validate.py`
can run as a CI smoke check (`--duration-hours 0`) or as a production-like soak
(`--duration-hours 24`, optionally 72). Every sample is appended to JSONL so an
interrupted run remains useful. Samples include Docker Compose service state and
the API health snapshot: processed block, block lag, events/sec, decode errors,
audit queue depth/latency, provider health and SSE client count. A summary also
performs duplicate-identity, point-in-time and index checks when PostgreSQL is
reachable.

The event bus now keeps a bounded event-id set. Redis retries and cross-instance
redelivery with an existing `event_id` are idempotent and cannot fan out a second
SSE event. Existing WS/HTTP failover, Last-Event-ID replay, provider circuit
breakers, worker-owned forward observations and token-scoped intelligence
refresh are exercised by the Phase 7.8/7.9 regression suites.

## Runtime topology

```text
BSC WS -> HTTP fallback -> bsc-ingestion -> PostgreSQL
                                      \-> RadarCandidate -> radar-worker
PostgreSQL <- forward-worker <- LiveSignalObservation
API <-> Redis pub/sub <-> API instances
Web -> API /api/radar/events (SSE)
```

Run the long soak beside the compose stack:

```bash
docker compose up -d postgres redis db-migrate api bsc-ingestion radar-worker forward-worker web
python scripts/phase795_validate.py --duration-hours 24 --interval-seconds 60
```

For a CI smoke check:

```bash
python scripts/phase795_validate.py --duration-hours 0 --report data/reports/phase7_9_5-smoke.jsonl
```

## Release gate

`scripts/release_pipeline.py` runs pytest with an isolated basetemp, Ruff, the
frontend production build, clean artifact packaging and a fail-closed audit of
the artifact. It writes `data/reports/phase7_9_5_release_gate.json`. The release
artifact excludes `.env`, `.git`, dependencies, tests, caches, databases and
temporary Phase directories.

## Benchmark and runtime results

The repository contains deterministic smoke checks and benchmark hooks; actual
24/72-hour values are environment-dependent and must be captured by the soak
command. The JSONL report records the evidence needed for processed blocks,
reconnect/fallback counts, decoded/failed/orphan events, candidates, audit jobs,
signals, memory and service health. No production result is claimed until that
report is attached to the release.

## Known limitations

- A real BSC soak requires configured RPC/WS endpoints and running PostgreSQL and
  Redis; unavailable services are reported as `UNAVAILABLE`, not fabricated as
  healthy.
- PostgreSQL `EXPLAIN ANALYZE` must be run against representative production
  data. The validator checks required indexes and duplicate/PIT invariants but
  does not invent query plans on an empty database.
- Memory leak detection remains an operational observation: collect container
  RSS/task/connection metrics during the soak and review the JSONL timeline.

Phase 8 is intentionally out of scope.

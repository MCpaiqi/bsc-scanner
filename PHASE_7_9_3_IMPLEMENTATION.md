# Phase 7.9.3 - Production Radar Observability

## Scope

Production Radar now exposes one source-health registry for BSC WebSocket, BSC
HTTP, PostgreSQL, Redis, DexScreener, GoPlus, Audit Queue, and SSE. Each source
reports `HEALTHY`, `DEGRADED`, `STALE`, `DOWN`, or `UNKNOWN`; the registry keeps
bounded counters, timestamps, latency, error, rate-limit, and cache-hit data.

The public diagnostics surfaces are:

- `GET /health` (backwards-compatible dependency fields plus `sources` and `metrics`)
- `GET /diagnostics`
- `GET /api/radar/diagnostics`
- `GET /api/radar/health`
- `GET /api/bsc/stream/status`

Diagnostics payloads redact URLs, credentials, endpoint values, and arbitrary
provider payloads. They expose operational counts only.

## Metrics

The in-process metrics registry covers chain head, processed block, block lag,
ingestion and decode-failure rates, candidate discovery rate, audit queue depth,
audit latency/failure rate, provider latency/error/429/cache-hit rates, and SSE
client/event rates. Provider metrics are retained in bounded rolling samples and
are also available in flat aliases for lightweight clients.

## Freshness contract

Radar candidates, smart-money snapshots, risk snapshots, early buyers, and market
feed payloads include `observed_at`, `updated_at`, `freshness`, and where useful
`data_freshness_seconds`. Missing timestamps are `UNKNOWN`; stale or expired
records are labeled in the API and React workstation and are never presented as
live data. Provider outage responses use stale cache only within the configured
stale window and mark the payload `STALE`.

## Resilience

External evidence and market feeds use bounded stale-cache fallback, exponential
backoff, and a circuit breaker (`CLOSED`, `OPEN`, `HALF_OPEN`). Three consecutive
failures open the default circuit; a timed half-open probe closes it after a
successful recovery. BSC WebSocket ingestion can fail over to HTTP polling while
the chain lane remains available. Provider failures do not stop Smart Money,
Risk Engine, canonical discovery, or the audit queue.

Redis event fanout and SSE delivery remain best-effort; PostgreSQL and chain facts
remain canonical. Trading is unchanged and remains disabled by default.

## Operational UI

The React workstation now has a Health / Diagnostics view with source status,
freshness, latency, errors, chain lag, ingestion, audit, provider, and SSE
metrics. The regular source strip includes all eight registry entries. No API
keys, secrets, or private endpoints are rendered.

## Tests

`tests/unit/test_phase7_9_3_observability.py` verifies source transitions and
redaction, metric coverage, circuit recovery, missing/stale/expired freshness,
and market-provider outage fallback. Existing WebSocket reconnect, HTTP fallback,
Redis/database health, audit queue, and external-provider tests remain in the
targeted suite.


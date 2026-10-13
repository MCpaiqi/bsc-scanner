# Phase 7.8.5 — Real-Time Radar API

Implemented the real-time radar contract for the canonical candidate projection.

## API

GET /api/radar/candidates supports status, priority/minimum priority, smart-money score, risk, age, launchpad, liquidity, volume, pagination (limit/offset) and deterministic sorting (priority, age, liquidity, volume). Responses include as_of, data_freshness_seconds, source_status, total, and page metadata.

GET /api/radar/candidates/{chain}/{token} includes freshness/source metadata. GET /api/radar/stats reports status counts and snapshot metadata; GET /api/radar/health reports canonical DB status, Redis degradation and SSE client/buffer metrics.

## SSE

GET /api/radar/events emits Server-Sent Events with id, event, and JSON payload fields (event_id, event_type, chain, token, occurred_at, data). Supported events include candidate discovery/update, smart-money, early-buyer, risk/signal, audit and source degradation notifications.

The in-process event bus uses a bounded ring buffer and per-client bounded queues; slow clients drop oldest messages and never block ingestion. Heartbeats are emitted every 15 seconds. Disconnect cleanup removes subscriber queues. Last-Event-ID replays buffered events where available. Optional Redis publish is best-effort for cross-instance fanout; Redis outages degrade health but do not affect canonical database state or ingestion.

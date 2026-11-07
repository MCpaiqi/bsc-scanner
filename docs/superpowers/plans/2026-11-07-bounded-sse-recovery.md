# Bounded SSE recovery implementation plan

> **For agentic workers:** Use superpowers:executing-plans inline; request one fresh read-only review after implementation.

**Goal:** R7 provides explicit bounded replay/reset semantics under bursts, slow clients, API restart and Redis interruption; the browser recovers canonical snapshots without applying stale event payloads as research results.

**Spec:** docs/RESEARCH_RELIABILITY.md R7, preserving the complete R1–R14 programme and verified GitHub delivery.

**Architecture:** Retain bounded in-memory history and per-client queues. Replay a retained cursor only when matching events fit; otherwise send an unfiltered STREAM_RESET control frame with the latest resume cursor. Queue overflow coalesces into one reset until consumed. Initial API connections and broker gaps require resnapshot. Redis reconnects with bounded delay, clears untrustworthy history and reports link availability. Browser events are hints, deduplicated in bounded memory; reset/reconnect triggers canonical REST refresh with a generation fence, visible synchronization state and research-only selected-detail refresh.

**Approaches:** Exact durable replay would require a proven cross-process commit/delivery ordering and retention contract, which the existing outbox does not promise. Silently dropping old messages preserves a bounded queue but hides gaps. Chosen: bounded replay plus explicit resnapshot, matching a current-state research dashboard while keeping event history/audit separate.

**Ruling:** Existing R7 requirements and continued implementation authorization resolve the design choice; no additional approval step. The existing tracked frontend omits application entry points/dependencies; include its necessary existing source in this batch so the published recovery client can build. Preserve assets/data/private configuration. R12 CI/dependency/migration reproducibility remains broader than this source closure.

**Tech stack:** Python/asyncio/FastAPI/Redis, React/TypeScript/Vitest, existing package lock; no new dependencies or schema migration.

## Review focus

- A >256-event replay and slow filtered subscribers must receive an explicit reset, never QueueFull or silent loss.
- Cursor lookup and subscriber attachment must be atomic; reset control bypasses domain filters and SSE cursor framing remains valid.
- Broker loss/reconnect and malformed messages must not permanently stop the bridge or leave old history falsely replayable.
- Late/duplicate historical event payloads must not overwrite canonical financial/risk state; reset during an in-flight fetch must reject that older response.
- Browser transport-open, broker availability, snapshot refreshing and refresh failure must remain distinct; selected-detail refresh must preserve annotation drafts.
- All new source dependencies needed by the browser entry point must be in the scoped commit; no generated builds, credentials or local data.

## Tasks

- [x] Add RED backend regressions for bounded replay, unknown/expired cursors, slow-client coalescing, filters, initial reset and broker recovery/malformed input.
- [x] Implement bounded subscription/reset controls and a persistent Redis bridge; keep direct internal subscriptions compatible unless recovery is requested.
- [x] Add RED client/page/detail tests for duplicate hints, reset handling, stale fetch rejection, visible recovery failures and retained drafts.
- [x] Implement client recovery and snapshot-only research rendering, include necessary existing frontend source, and verify build closure in an isolated export.
- [x] Verify backend/Redis cross-process reconnects, full backend and frontend tests/typecheck/build, Ruff/whitespace and one review/fix pass; update ledger, scoped commit and push. Final backend743 passed/34 skipped, frontend50 passed, PostgreSQL/Redis30 passed;38-file frontend export installs/builds/tests independently.

# Phase 7.8.1 — Canonical Radar Candidate Engine

## Scope

This phase makes SMMS chain intelligence the source of truth for the live radar
universe. External market feeds may enrich an existing candidate, but they cannot
create one. GMGN, GoPlus, and the Audit Queue worker remain out of scope.

## Pipeline

[91m[0m```text
canonical chain event
  -> RadarCandidate (one per canonical Token)
  -> fast, bounded feature aggregation
  -> deterministic Radar Priority Score
  -> read-only radar API projection
[91m[0m```

Supported discovery evidence includes pair creation, Flap lifecycle creation and
launch events, the first meaningful buy, and meaningful native market activity.
Evidence is idempotent and retains raw-event provenance where available. Removed,
orphaned, deleted, or ambiguous evidence is invalidated and candidates are
re-evaluated fail-closed, which makes restart recovery and reorg handling safe.

## Domain and persistence

[91m[0m`RadarCandidate[91m[0m` references the existing [91m[0m`Token[91m[0m` row and stores lifecycle state,
stage, bounded feature and score projections, latest intelligence snapshot links,
and timestamps. [91m[0m`RadarCandidateHistory[91m[0m` records every legal status transition;
[91m[0m`RadarCandidateEvidence[91m[0m` records the canonical source facts and raw-chain proof.
The unique token reference prevents duplicate candidates for one canonical token.

Statuses are a typed state machine: [91m[0m`DISCOVERED[91m[0m`, [91m[0m`PREQUALIFIED[91m[0m`, [91m[0m`WATCH[91m[0m`,
[91m[0m`INTERESTING[91m[0m`, [91m[0m`HIGH_SIGNAL[91m[0m`, [91m[0m`HIGH_RISK[91m[0m`, [91m[0m`REJECTED[91m[0m`, and [91m[0m`STALE[91m[0m`. Transitions
require a [91m[0m`CandidateStatus[91m[0m` value and a non-empty reason; arbitrary strings are
rejected.

## Fast features and priority

Aggregation is limited to recent normalized events and current, versioned
smart-money/risk snapshots. Missing values are represented as [91m[0m`UNKNOWN[91m[0m`, never as
zero. The priority score is not a trading signal: it is deterministic,
configurable, and explainable through returned components (recency, buy pressure,
smart money, liquidity, and risk adjustment) plus the active configuration.

## API

* [91m[0m`GET /api/radar/candidates[91m[0m` supports status, minimum priority, launchpad, risk,
  age, chain, and limit filters.
* [91m[0m`GET /api/radar/candidates/{chain}/{token}[91m[0m` returns one aggregate projection.

Responses intentionally omit database identifiers and raw payloads.

## Verification

The Phase 7.8.1 suite covers pair/lifecycle/trade discovery, idempotency,
orphan/reorg handling, restart persistence, unknown handling, score explainability,
state transitions, and API filtering. Full repository verification passed with
[91m[0m`175 passed[91m[0m`.

# Live Alpha coverage and recovery

The live recipe is `experimental-v4`, with labels `alpha-shadow-v3`. Earlier
scores and labels remain stored under their original versions. Run Alembic
through `20261109_0046` before starting the updated application or workers.

`alpha-coverage` is an independent Compose worker. It scans each admitted
PancakeSwap pair from its creation block through the first 65 minutes using
confirmed HTTP Swap queries, in chunks of at most 1,000 blocks. It requires a
configured, deployed factory and a BSC HTTP provider. Missing configuration is
reported as degraded. It does not require Redis or the Forward worker.

Discovery registers one coverage job and four pending snapshots in the discovery
transaction. A bounded cyclic sweep repairs imported/legacy pairs, including IDs
that commit out of order. Jobs and leases survive restarts. Failed RPC queries,
decode failures, missing callers or incomplete canonical persistence retry with
safe error codes; provider URLs and credentials are not written into receipts.

Each successful scan commits its RawChainEvent, MarketEvent, TradeEvent and
coverage receipt together. A successful empty query is useful evidence. A receipt
identifies the chain, pair, protocol, topic, confirmed block bounds, boundary and
parent hashes, timestamps, row counts and coverage generation. Proof assembly
requires the creation hash and block-adjacent, parent-linked receipts; gaps,
wrong query scope, partial decoding and invalidated generations are excluded.
Malformed RPC `null` results or non-object log items are errors, never empty
successes. A persisted canonical publication fence serializes reconciliation
with receipt commits, including a first scan with no existing raw logs or receipt.
Only reconciliation writes block anchors; stale collectors cannot overwrite them.
Older reconciliation requests cannot replace newer observations.

Pending input has a NULL score. A snapshot is scored only after entry coverage
and an executable VWAP are available. Units, exact entry price, quote notional
and its policy fingerprint are pinned. A pending or tracking snapshot remains
due after long downtime; no rolling 15/75-minute filter discards it.
Canonical rediscovery resumes and re-anchors unscored work. Entry observations
include Swap events timestamped in the pair's creation block.

An observed target hit may be shown while the future window is incomplete.
An unhit target remains UNKNOWN until the full future hour is covered and the
horizon has matured. Only then is it False. Rates use one 1-minute observation
per token/pair with verified entry and full-hour coverage under the configured
notional fingerprint. Other windows and partial positives do not enlarge the
denominator. Counts and rates are aggregated in SQL.

The score is an uncalibrated activity ranking, not an outcome probability.
Momentum (25), participation (25) and observed buyer concentration (20) supply
70 available points, normalized to 0–100. If liquidity exists it adds 15 points
and the denominator becomes 85. No-sell covered observations can supply buy
pressure; missing concentration supplies zero. Risk remains UNKNOWN and an
observed rapid-fire penalty remains explicit. The high-score threshold is 70.

Reorg reconciliation invalidates affected receipts and clears trusted labels;
scored historical inputs are retained as INVALIDATED and corrected Alpha revisions
are enqueued automatically; see [reconstruction and lineage](ALPHA_RECONSTRUCTION.md).
Other derived snapshot reconstruction remains part of the open R3 gate. Transactional
projection/outbox and bounded SSE recovery are recorded separately in the programme
ledger. Full Compose restart and real-provider archive availability remain open.
Canonical anchors now have lossless hot/archive retention; see
[storage, safety and audit](CANONICAL_ANCHORS.md). Dense provenance storage,
publication-lock latency and asynchronous reconstruction throughput remain part
of the wider R11 performance gate.
An unavailable archive cannot be turned into a completed negative result.

Verification uses `tests/unit/test_alpha_coverage_integrity.py`,
`tests/unit/test_alpha_collector.py` and
`tests/integration/test_alpha_policy_migration.py`. Explicit
`SMMS_RESEARCH_TEST_DATABASE_URL` / `SMMS_MIGRATION_TEST_DATABASE_URL` point these
tests at an isolated PostgreSQL database; each owns and removes a unique schema.
No live RPC or production database is used by these tests.


## Dense-input execution and rollout

The v4 recipe uses `exact-scaled-integer-v1` VWAP totals. Each eligible trade is
resolved once, equal timestamps enter together, and a sliding sweep tests (start,end]
windows after sorting. Any executable30s window is preferred to60s fallback; ties
keep the first anchor. HUMAN quote notional still gates both HUMAN and RAW bases.
Exact totals prevent losing a small remaining amount when a large trade expires,
and avoid rounded notional gates. Final division still follows Decimal context.
Because boundary results can change, v3 snapshots are retained under their original
recipe and excluded from the new v4 cohort; historical exports use alpha-labels-v2.
The new policy ID includes the arithmetic identity and exact notional text. Entry
and evaluation manifests identify the arithmetic explicitly. Rebuilding the active
cohort does not overwrite archived experiments or mix their denominators.

Only due pending/tracking snapshots load price inputs. One batch shares pair/job/
receipt/trade reads and isolates each pair/token/window. SQL predicates are chunked
at200 ranges; exact data inside a valid horizon is not silently truncated. A bounded
cyclic repair remains necessary for late commits; PostgreSQL maintenance ownership
is schema/component/version scoped and nonblocking, including first startup.

# Bounded research runtime implementation plan

> **For agentic workers:** Use superpowers:executing-plans inline; one fresh read-only review at the end.

**Goal:** R11: due-task evaluation, batched price/fact reads, non-quadratic VWAP and durable observable iteration latency, while retaining research meaning and history.

**Spec:** docs/RESEARCH_RELIABILITY.md R11 under the original BSC/PAPER research desk objective. R3/R12–R14 remain open. Preserve local data and user-authorized verified pushes on the existing branch.

**Evidence:** 200 same-timestamp trades resolve the price basis40000 times;400 trades resolve160000 times (0.134s→0.665s locally). max_rolling_vwap scans every trade at every event anchor. Alpha fetches entry/future trades, jobs, coverage receipts and manifest receipts per snapshot; _features repeats its wallet total per wallet. Forward already has bounded durable due jobs and a batched USD loader, but long OR lists need chunking. Alpha/Forward cyclic repair intentionally handles sequence/commit-order inversion and must remain bounded, not be removed. Alpha records process-local timings but API health does not expose them; forward-worker heartbeat currently has no cycle timing/outcome.

**Approaches:** Retaining repeated scans with fewer calls still has quadratic dense-window work. Sliding Decimal sums can lose tiny remaining amounts after a large trade leaves, because ordinary addition/subtraction rounds. Chosen: resolve each eligible trade once, maintain exact scaled-integer totals and divide at the Decimal boundary; group equal timestamps before evaluating inclusive endpoints. Batched input loaders share SQL reads and use indexed per-subject window slicing. Runtime persists a small explicit phase/cycle report in its heartbeat and exposes a whitelisted health summary across processes.

**Rulings:** Existing autonomous continuation authorizes this design without another approval round. Preserve (start,end], equal-timestamp inclusion, first-timestamp ties, HUMAN notional gating, RAW/HUMAN separation and the existing preference for any executable30s window before considering60s. Exact totals can correct rounding edges, so do not relabel the existing experiment: new live recipe experimental-v4 / alpha-shadow-v3 and historical labels alpha-labels-v2, with explicit arithmetic policy identity. Old experiment rows/manifests remain retained and are excluded from the new active cohort; canonical invalidation must cover archived versions too. Cyclic repair remains bounded to recover late commits; claims use skip-locked ownership rather than blocking another worker. No provider calls, deployment or production result rewrite.

**Files/interfaces:**
- New src/smms/alpha/vwap.py: WindowPoint(timestamp,quote,token,notional), exact vwap and max-window evaluation; baseline wrappers own price-basis resolution.
- New src/smms/alpha/inputs.py: batch pairs/jobs/receipts and exact pair/token/time trade ranges for selected due snapshots; indexed window slicing, max200 predicates per query.
- alpha/live.py, work.py, lineage.py: reuse loaded jobs/receipts/trades without changing proof predicates or manifest facts; preserve canonical/cutoff/generation fences. _features computes its wallet total once.
- radar/forward_prices.py: chunk merged exact ranges without duplicate rows, changing neither units nor cutoffs. Existing evaluate_due pending/retry/claim semantics remain.
- runtime.py: run one measurable Forward/Alpha cycle with committed counters, phase/error/duration reporting and retry after transient DB failures; cancellation propagates and rolled-back counts are not presented as committed.
- api/app.py: _worker_health returns a validated forward_worker research summary from durable heartbeat details; never expose arbitrary worker details/secrets.
- policy.py/baseline.py/model default and Alpha docs/version tests: separate arithmetic cohorts and retained legacy audit behavior.

## Review focus

- Duplicate timestamps, sparse30/60s fallback, invalid-amount anchors, exact window edges and tie resolution preserve execution-window semantics.
- Large expired values/tiny surviving amounts, RAW uint amounts, decimal context and notional thresholds cannot drift because of a performance shortcut; precision changes have explicit recipe identities.
- Batched reads cannot mix pairs/tokens, include future/noncanonical facts, drop receipt proof checks or omit observations after a commit-order inversion.
- Completed/not-due cohorts do not trigger repeated price evaluation; due claims remain bounded/fair and concurrent repair ownership cannot stall independent claims.
- API health in a separate process sees meaningful phase/latency/committed counts, distinguishes failed/rolled-back cycles, and never trusts arbitrary heartbeat payload fields.

## Tasks

- [x] Add RED VWAP semantic/dense-work regressions and independent Decimal/rational reference cases; implement exact sliding windows and versioned cohort rollout.
- [x] Add RED Alpha SQL-count/window/canonical tests and large Forward range tests; implement bounded batched inputs and receipt/manifest reuse, retaining existing proofs.
- [x] Add regression evidence for completed/not-due cohorts, late commits and concurrent repair; change only demonstrated maintenance/claim gaps.
- [x] Add RED runtime/health tests for durable timings, failures, rollback and cancellation; implement phase/iteration reporting and safe API exposure.
- [x] Run full backend/frontend, real PostgreSQL concurrency/version checks, dense-input benchmark, build/Ruff/whitespace and one fresh review/fix pass; update the ledger for scoped commit/push.


## Scope audit and review rulings

Ruling: the earlier ledger/Alpha docs also assign canonical anchor/provenance retention,
publication-lock latency, dense history retention and asynchronous reconstruction throughput
to R11. They are not removed by the four summary clauses. This batch advances due-work,
batched inputs, exact VWAP and durable main-cycle telemetry; R11 remains OPEN for the wider
retention/publication/reconstruction closure. No hard wall-time or memory SLO is claimed,
and valid dense-window facts are not truncated to satisfy an artificial budget.

One fresh read-only reviewer found one Important fault: malformed heartbeat phase/error
shapes and a UTC-conversion-overflow timestamp could break /health. All three were reproduced
RED through the real endpoint, then fixed with typed enum checks and overflow-safe timestamp
validation. No Critical or deferred Minor findings. The review otherwise confirmed the window,
proof, batching, versioning and repair-lock boundaries.

Final: Ruling: exact totals correct prior rounding edges, while final Decimal division stays
context-dependent — recipe/policy identities separate the new cohort — old experiment values
are retained, not silently recomputed or merged with v4 statistics.
Final: Ruling: bounded cyclic registration revisits historical subjects to recover commit-order
inversion — only due snapshots fetch pricing inputs, and completed/not-due regression requires
zero trade queries — metadata reconciliation still consumes its bounded page budget.
Final: Ruling: stage-boundary heartbeats and completed timings are observable across processes;
last-committed reports belong to the current instance — no continuous timer during synchronous
CPU work or cross-restart aggregate history is claimed — long stages can become visibly stale.
Final: Ruling: provenance retention, publication latency, asynchronous rebuild throughput,
R3 integrity audit and R12–R14 remain open — these original requirements are not certified by
the current performance tests — the overall goal stays active.

Eight old test assertions relabelled non-UTC PostgreSQL datetimes instead of converting their
instants. The observed eight-hour mismatches were fixture assertions; tests now use utc().
No product timestamp semantics were changed to make those checks pass.

The real upgrade fixture now distinguishes a new recipe from an appended revision:
v4 starts at revision0 beside retained v3, then invalidating a scored v4 input and
rebuilding the same recipe creates revision1. The existing downgrade guard is exercised
only after that append. This does not broaden the guard to all revision0 manifests;
full migration/downgrade archival policy remains part of R12.
Using the global coverage invalidation in this fixture also triggered the newer
Derived research guard before the Alpha guard; the fixture uses the targeted Alpha
invalidation helper so it verifies the intended guard without disabling other guards.

Final evidence: backend817 passed/42 skipped; PostgreSQL113 runtime/concurrency/
reconstruction checks plus1 final upgrade/downgrade check passed. Changed Forward
timestamp assertions passed17 SQLite checks/4 skips. Unchanged frontend passed76
tests and TypeScript/production build; Ruff/whitespace passed. Zero owned schemas/
other database clients were confirmed before stopping the foreground instance;
its terminal process exited successfully. R11 remains OPEN at its wider scope.
Delivery uses the user-authorized scoped commit and push on codex/research-integrity.

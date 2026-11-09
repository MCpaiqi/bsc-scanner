# Canonical anchor archive implementation plan

> **For agentic workers:** Execute inline with superpowers:executing-plans and TDD; one fresh read-only review at the end.

**Goal/spec:** Advance original docs/RESEARCH_RELIABILITY.md R11 retention and canonical publication latency without losing old-fork fencing or historical evidence. R3/R11–R14 remain OPEN until their full requirements are proven. User authorizes autonomous verified scoped commits and pushes on codex/research-integrity.

**Evidence:** AlphaBlockAnchor retains one mutable row per reconciled height forever. It is a canonical publication fence, not disposable cache: deleting old entries would let a stale confirmed scan lose a known fork conflict. Current anchor_blocks serializes a global fence and performs per-height inserts/selects. Provenance manifests/receipts also retain full facts; they are distinct obligations and must not be erased to make anchor counts look bounded.

**Architecture:** Retain a recent hot window (default4096 blocks) and compact older complete512-height buckets into immutable compressed canonical-anchor segments. Each segment includes exact height/hash/UTC observation time, chain/bucket identity, row count and SHA256 of bounded deterministic uncompressed JSON. Missing heights remain absent. Corrections to archived blocks go into the hot table, override only when at least as recent, then append a new segment revision. Old segment revisions remain retained. Archive reads select only the newest segment per requested bucket, validate bounded decompression/digest/schema, and merge hot rows by observation time. Corrupt archive data fails closed.

**Ruling/approaches:** TTL deletion loses research safety. Keeping every indexed hot row has indefinite index growth. Chosen lossless archive preserves current fences plus old segment revisions, bounds one maintenance operation to one512-height bucket, and reduces hot/index footprint after backlog drains. Total evidence still grows with chain history; this is not a fixed total-disk policy or manifest deduplication. No production DB writes, provider calls, deployment or merge. Existing autonomous continuation opts out of another design approval round.

**Interfaces/files:**
- db/models/alpha.py + __init__.py/base.py: immutable CanonicalAnchorSegment, unique(chain,bucket,revision), payload/metadata bounds. Existing hot model remains unchanged.
- db/migrations/versions/20261109_0046_canonical_anchor_archive.py: additive table/index only, no hot deletion/backfill. Online downgrade refuses any retained segment; offline downgrade refuses an unverifiable archival check.
- alpha/anchors.py: Anchor value, encode/decode, latest requested bucket reads, lock_fence, archive_anchors(session, keep_recent_blocks=4096, now=...). One complete eligible bucket per call. Copy/checksum/write/delete hot rows in one fenced transaction; conditional deletion matches the observed original values. Archive failure/rollback leaves hot evidence intact.
- alpha/work.py reexports anchor_blocks from anchors.py, preserving all callers. Batch requested hot reads and new inserts in bounded200-size chunks. Preserve absent-block fence, old-observed rejection, orphan markers and changed-height semantics across archived/hot values.
- runtime.py/config/settings.py: a cohosted anchor archive cycle with its own small source transaction after each reconstruction cycle; independent anchor-archive heartbeat, committed counts and retryable fixed errors. Maintenance skips on PostgreSQL lock contention inside a bounded SAVEPOINT, and never holds the canonical fence across heavy reconstruction work. Default hot retention configurable at CANONICAL_ANCHOR_HOT_BLOCKS (>=512).
- research/telemetry.py + api/app.py/research.py: explicit anchor-archive health profile and bounded audit metadata/segment endpoints. Full verified segment requested separately, no blob on normal candidate/health responses.
- tests/unit/test_canonical_anchor_archive.py, tests/unit/test_anchor_archive_runtime.py and tests/integration/test_anchor_archive_migration.py: semantic/atomicity/corruption/concurrency/upgrade safety; keep existing coverage/reorg tests.
- README, docs/ALPHA_COVERAGE.md, docs/api.md and ledger: head0046 and precise retention/rollout/remaining scope.

## Tasks and review focus

- [x] Task1 RED codec, roundtrip/corruption/bounded decode, old archived fork conflict, observation ordering, absent-height behavior and bounded anchor SQL tests; implement storage/read/write primitives and models.
- [x] Task2 RED archive move/rollback/correction history/immutable guard and real PostgreSQL fence/concurrency cases; implement one-bucket compaction, keeping recent/incomplete buckets and conditional-copy safety.
- [x] Task3 RED runtime commit/error/cancellation and separate audit/health read cases; implement cohosted independent cycle/profile, settings and migration upgrade/refused downgrade tests.
- [x] Task4 full backend, real PostgreSQL coverage/reorg/concurrency/migration, Ruff/whitespace and footprint/backlog evidence; one fresh read-only review/fix pass; update ledger, stop owned DB and prepare scoped verified delivery.

Review deliberately: stale collectors before/after compaction; deep correction of archived heights; missing heights and timestamps; byte/schema/digest corruption; same-bucket concurrent archivers and canonical writers; cancellation between copy and delete; archive hash compatibility and no JSON/Decimal recipe change; actual hot-window bound includes at most511 incomplete-bucket heights plus undrained backlog; disk is not globally capped; archive metadata visibility does not certify live providers or full-topology restart.

## Pre-flight/rulings

Existing isolated codex branch is retained. Windows skill helper scripts are POSIX; use this plan plus its own ignored scratch workspace for progress/evidence. Do not clean user data or old untracked releases/reports. Task1 reader is required before Task2 removes any hot row. Task3 invokes compaction in a separate source transaction so canonical locks cannot span heavy rebuild computation. Migration0046 must precede every updated reader/writer; mixed binaries and production rollout remain R12/R14 acceptance work. Canonical anchor archive closure is one part of R11, not a substitute for dense provenance/manifests and source streaming.

Task1 initial14 RED cases: missing codec/archive interfaces and600-height publication issued1802 SQL statements. Codec/retention/SQL cases went GREEN; the600-height case now requires at most15 statements. insert_once now accepts a positional mapping/list so existing single-row callers remain compatible with bounded bulk inserts. Storage/lookup semantics preserve source timestamps, missing heights and old-fork conflicts; no score/label recipe change.

Task2 verifies sparse buckets, old revision retention, newer/older observations, rollback, ORM immutability, two archivers, busy publication and conditional deletion. A real uncoordinated external correction between copy/delete makes the job roll back its archive while retaining every hot row and that correction. Async cancellation after segment write but before hot deletion leaves1536 hot rows/zero segments, then a fresh cycle succeeds.

Task3 initial5 RED runtime/audit/health cases and a RED migration test became GREEN. Two additional RED cases exposed premature ARCHIVED health state and archiving within a larger reconciliation window: committed gating and effective retention max(configured hot window, reorg lookback+1) fix them. The bounded decoder also normalizes an injected JSON parser nesting error to retryable ValueError; the ordinary deep invalid-shape case already failed closed on local Python3.13. This is parser fault injection, not a claim of Python3.12 verification.

Migration-head ruling: a real0046 upgrade exposed hardcoded current-head0045 in the old analyst migration test (RED). Shared migration_head now derives the actual graph head for10 current-head assertions, while explicit historical upgrade/downgrade targets remain intact. The complete PostgreSQL integration selection passed24/2 skipped, including all migration tests; skipped live-network checks remain outside this batch. This repairs test freshness, not production revision metadata.

Evidence before final review: PostgreSQL129 runtime/coverage/collector/research/derived cases passed; PostgreSQL29 new archive/runtime cases passed after decoder/async-BUSY additions. Initial full backend853 passed/60 skipped; a fresh final run follows current decoder changes and review. Ruff passed. Retention does not delete provenance/receipts/manifests, and total historical disk growth is not capped.

## Final review and fix-pass rulings

One fresh read-only reviewer found one Important issue and no Critical/Minor:
the COMMIT heartbeat await retained the outer transaction's global canonical
fence for up to2s, even on IDLE cycles. A blocked-heartbeat/independent-publisher
regression reproduced RED. The intermediate await is removed; commit/rollback
releases the fence before reporting. Both ARCHIVED and IDLE cases are verified.

The parent regraded the review's declined ingestion-lifecycle concern after a
new archive-specific reproduction: corrupt archived bytes cause reconciliation
to retain its canonical transaction after recording failure. The independent
publisher then times out (RED). A reconciliation-owner context now rolls back
before releasing the shared AsyncSession lock, for validation errors and
cancellation. This fixes the archive-specific path within the same final fix pass;
it does not certify every older ingestion exception path. No second review.

Final: Ruling: synthetic footprint/backlog evidence supports this implementation,
not a production throughput SLO or fixed total disk bound — all history/corrections
remain retained and whole-path production sizing stays OPEN R11/R14.
Final: Ruling: migration-first/stopped-old-binary rollout and consistent whole-DB
backup/restart remain R12/R14 — a mixed old reader can ignore cold evidence, so the
new protocol must not be activated alongside old binaries.
Final: Ruling: raw SQL administration is outside ORM immutability; conditional
copy guards and fail-closed verification still protect tested unexpected mutations
— coherent malicious replacement is not prevented by an application guard.
Final: Ruling: SHA256 verifies internal format/integrity, not signed authenticity
against an administrator rewriting both payload/checksum — provider/canonical
validation remains a separate source contract.
Final: Ruling: broader pre-existing ingestion lifecycle behavior is not certified
by the targeted archive failure fix — R3/R14 final audits remain required.
Final: Ruling: live providers, frontend and full Compose were not exercised in
this backend-only batch — their original acceptance gates remain unchanged.
Final: Ruling: reviewer was strictly read-only and did not rerun fixtures — parent
command outputs supply the execution evidence; no independent execution is claimed.
No deferred Minor findings.

PostgreSQL realistic-hash footprint: first16384 hot rows drain in24 bucket moves
to4096; append16384 more (20480 hot) drains in32 moves back to4096. After a deep
correction,57 segment revisions retain all cold evidence/old revision. Five no-op
cycles add no segments. Payloads1251692 bytes vs3204045 raw JSON; operation times
2.744/3.576s are local synthetic observations. Hot logical row bytes1966077 at
initial16384 vs491517 at final4096 describe row counts, not comparable total file
size; PostgreSQL allocated files are not claimed to shrink. Evidence/schema stayed
in an owned temporary PostgreSQL database, without provider or production writes.

Final verification after both lock fixes: backend855 passed/64 skipped/14 warnings
(existing deprecation/reflection categories, including the new head-helper emission);
PostgreSQL135 passed; complete integration24 passed/2 live-network skips. Ruff and
whitespace passed. Foreground PostgreSQL stopped after confirming zero owned test
schemas/other clients, and its process exited0. No frontend source, provider,
production migration/write, deployment or merge was changed. Scoped commit/push
remains the authorized delivery step; verify origin SHA before reporting it. R3
and R11–R14 remain OPEN under the original whole objective.

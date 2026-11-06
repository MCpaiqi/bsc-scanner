# Wallet/token source integrity

Continue the active BSC reliability programme inline on codex/research-integrity.
Use RED→GREEN regressions, existing research publication/visibility/rebuild seams,
one fresh read-only review and a scoped local commit. Preserve untracked files.

## Design

Refresh retained raw Trade/Pair values before calculating or manifesting a new
generation. Fence epoch changes and bind token aggregation to the exact wallet
snapshot/score actually used. A preserved newer publication winner cannot be
silently referenced alongside an older computed profile; retry that source read.
Do not hold the research epoch writer lock during replay/publication.

Version the corrected canonical USD input recipe independently from the unchanged
score-v2 weights and profile-v1 storage container. Native/quote amounts and generic pool
prices cannot act as USD. Normalize finite nonnegative cash flow and explicit or
derived USD marks; missing quantities/valuations remain unknown. Missing flows
must survive aggregation, storage and API serialization as null/UNKNOWN, not zero.
Scoring may use documented neutral inputs for unknown metrics with coverage-based
confidence; exported financial measurements must expose their unavailable state.

Migration0043 preserves old values, quarantines old wallet/token recipe outputs
and transitively dependent snapshots, and permits nullable unknown flow fields.
Durable reconstruction appends corrected input-policy revisions at original cutoffs
without claiming the former unsafe recipe reproduced. Known admission/window
configuration stays preserved. Unsupported recipes remain explicit audit-only data.

## Tasks

- [x] Reproduce stale identity maps across all refresh paths and mismatched winner binding.
- [x] Reproduce native/quote-as-USD and missing-flow-as-zero through public paths.
- [x] Implement refreshed/fenced/bound producers and versioned USD/UNKNOWN contracts.
- [x] Add preservation/quarantine migration and durable reconstruction integration.
- [x] Verify isolated PostgreSQL races, rollback/restart, API and full appropriate checks.
- [x] One review/fix pass, evidence ledger and scoped local commit.

R3 needs a final transitive audit before its checkbox can close. R6/R7/R9–R14,
including outbox/SSE, complete UI, dense-history retention, CI/access/topology and
provider risk-recipe compatibility, remain required programme work.

## Evidence and remaining scope

Backend688 passed/28 skipped/13 existing warnings. PostgreSQL comprehensive suite
initially141 passed/one old expectation failed; that legacy Radar/Signal fixture
expected UNKNOWN even after healthy CHAIN_EVENTS recovery. Updated it to require
the actual canonical token flow/score with LOW confidence; isolated recheck1 passed.
Full Ruff(src/tests), whitespace, Compose configuration, head0043 and offline
upgrade SQL1357 lines passed. One reviewer confirmed null-string propagation,
cash subtotals lost without quantity, and malformed auxiliary JSON failure; all
have regressions/fixes. Root regression also covers cached profile JSON readers.

Verified zero owned test schemas before removing the labelled container/volume.
Verification logs and prior temporary files remain outside the scoped commit;
preserve existing untracked product/data. No production migration, deployment,
push or real provider request. R3 remains open for raw-reader canonical filtering:
an isolated POST /api/wallet/replay reproduction included a removed trade. Fix that
and inspect adjacent legacy graph/replay readers next, then continue R6–R14.

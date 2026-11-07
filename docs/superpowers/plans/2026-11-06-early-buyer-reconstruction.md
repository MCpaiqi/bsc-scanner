# Canonical early-buyer and position reconstruction

Continue R3 under the existing reliability programme. Execute inline with
superpowers:executing-plans, meaningful RED→GREEN tests and one fresh read-only
review. This slice does not close Radar cache/Signal/Forward/dataset artifact
invalidation or the remaining R6–R14 requirements.

## Design

Use one canonical point-in-time replay input adapter for early buyers in the
historical builder, Radar signal/features and the public API. Capture the epoch
before reading facts; use observed canonical Pair creation, canonical trade and
market facts, transaction/log deduplication and an explicit as_of cutoff. An
unversioned Token creation/cache or a materialized early-buyer row cannot supply
research facts. Missing creation evidence stays UNKNOWN.

Extend the existing immutable derived snapshot contract to EarlyBuyer and
WalletTokenPosition: as_of, recipe hash, source epoch, revisions, exact input
manifest, audit history and bounded durable reconstruction. Preserve withdrawn
wallets as unavailable audit revisions instead of retaining a live stale buyer.
Legacy/imported inputs are quarantined and rebuilt under an explicit defaults
policy; no cached result values are overwritten or silently certified.

## Tasks

- [x] Reproduce stale buyer cache selection, orphan/future creation/trade leakage,
  market-only evidence, position lookahead and last-source withdrawal.
- [x] Implement shared canonical replay and integrate point-in-time consumers.
- [x] Add migration 0039, immutable publication and durable reconstruction for
  buyers/positions, preserving as_of/config and explicit unavailable states.
- [x] Verify aliases, history, restart, epoch races, migration preservation and
  refused lossy downgrade on isolated PostgreSQL.
- [x] Fresh read-only review, one regression-driven fix pass, required checks,
  ownership-checked resource cleanup and a scoped local commit.
- [x] Record actual evidence and full outstanding programme scope.

Previous batch was verified progress at 22a0469. Tracked checkout is clean and
pre-existing untracked source/data remain outside this batch.

Fresh reviewer reproduced four material gaps: retained ORM values certified with
a new epoch; raw Market units mixed with normalized Trade quantities; ignored
Trade.price_usd; address-based first-buyer truncation. All received failing
regressions before the single fix pass. The coordinator also bounded hot-path
entry reads, preserving UNKNOWN on unsaturated overflow, and corrected unknown
USD sums rather than publishing a zero. Review's remaining artifact/provider/
deployment exclusions retain their original R3/R11–R14 scope in the ledger.

Evidence: backend589/16 skipped; database61 (59 PostgreSQL + API SQLite + heartbeat
simulation); final migration guard3; web42/typecheck/build; Ruff/whitespace/Compose,
0039 head and offline SQL1222 lines. Zero owned schemas; labelled PostgreSQL
container/volume and temporary SQL removed. Scoped local commit only.

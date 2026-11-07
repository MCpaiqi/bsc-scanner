# Canonical Radar and Signal provenance

Execute inline with the existing superpowers:executing-plans / meaningful
RED→GREEN / one fresh read-only review workflow. Continue full R1–R14 scope in
RESEARCH_RELIABILITY.md. Previous goal turn was verified progress at 4ec72ad.
Tracked checkout is clean; preserve pre-existing untracked product/data files.

## Design

- Add immutable RadarFeatureObservation snapshots using the shared research
  epoch/revision/manifest/publication/visibility contract. A mutable candidate
  points at its current projection; stale cached fields cannot supply trusted
  lists, details, signals, audits, rules or Forward observations.
- Signal gains the same contract. Generate from a canonical point-in-time Radar
  snapshot rather than assigning a fresh as_of to candidate.features_json. Keep
  historical scores immutable; changed inputs append revisions, and old revisions
  remain available only in audit access. Price evidence must explicitly be USD.
- Capture epoch before source reads, refresh ORM source values, enforce raw and
  parent-pair provenance and original cutoffs. Candidate discovery/classification
  is reconstructed from canonical proof rows. A withdrawn candidate/Signal stays
  unavailable; legitimate losing activity remains in the observed universe.
- Extend bounded durable research jobs to Radar/Signal. Preserve as_of, configuration
  and logical identity; wait for invalidated prerequisite snapshots to rebuild,
  and never rewind a newer live candidate projection while rebuilding history.
- Migration 0040 preserves/quarantines old candidate projections and Signals;
  explicit legacy-defaults-v1 reconstruction does not claim unknown old parameters
  recovered. Refuse lossy downgrade and retain historical audit values.
- Stored/supplied CHAIN evidence requires current epoch and a validated source
  reference. Reject orphaned/stale proofs; external primary observations remain
  provider evidence. Risk checks chain evidence at its own captured epoch, so a
  pre-refresh resolution cannot be relabelled current after a reorg. Candidate
  views resolve stored observations rather than unversioned Token metadata cache.

## Tasks

- [x] Reproduce stale candidate/Signal/evidence reads, same-time score overwrite,
  source/price/cutoff errors and cross-session/reorg races with real regressions.
- [x] Add snapshot/schema/migration and central current/audit access contracts.
- [x] Integrate canonical Radar/Signal producers and candidate consumers; protect
  the existing Forward observation writer from stale pointers.
- [x] Add dependency-aware durable reconstruction and original-cutoff recovery.
- [x] Verify migration, concurrent writers, rollback/restart and no candidate
  rewind on isolated PostgreSQL; run full backend/web/required static checks.
- [x] One fresh read-only reviewer and one material fix pass; scope-reviewed local
  commit, ownership-checked resource cleanup and evidence ledger.

Forward entry/outcome reconstruction, materialized datasets/backtests and the
remaining R6–R14 programme are still required after this slice. This plan closes
neither their semantics nor their verification by association with Radar snapshots.

## Evidence and rulings

The fresh reviewer reproduced seven material issues; the single fix pass added
same-epoch transactional reverse dependency invalidation and publication fences,
preserved configured policy, normalized all chain aliases, excluded future market
observations, resolved newest provider observations, reconstructed delayed baseline
cutoffs and used a single-SELECT epoch guard. Existing regression coverage caught
and fixed wallet replay being incorrectly versioned by Token admission parameters.

Backend628/20 skipped/13 existing warnings; database104 (102 PostgreSQL, API SQLite
and heartbeat simulation); web42/typecheck/build; Ruff/whitespace/Compose/head and
offline SQL1285 lines. Zero owned schemas, labelled test container/volume and temp
SQL/logs cleaned. Scoped local commit only. Provider/risk field compatibility and
historical quote/USD source parity are explicitly retained as programme gates;
the R5 checkbox is reopened rather than treating arithmetic tests as end-to-end
extraction proof. Full programme scope remains in RESEARCH_RELIABILITY.md.

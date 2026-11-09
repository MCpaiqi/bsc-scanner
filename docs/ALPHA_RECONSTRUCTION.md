# Alpha research revisions and source lineage

Apply Alembic through `20261107_0045` before running the updated services.
This extends the [coverage contract](ALPHA_COVERAGE.md) with automatic Alpha
reconstruction. The full R3 programme also covers other derived snapshots and
materialized research; those remain open in [the ledger](RESEARCH_RELIABILITY.md).

## Revisions

A scoring recipe (`experimental-v4`) is separate from its input revision. Each
token/pair/window may have revision 0, 1, … under that recipe. `supersedes_id`
links a replacement to the earlier snapshot. The unique revision key makes
concurrent registration converge on one replacement per window.

Canonical invalidation retains old scores, entry price, features and components.
The invalidated outcome is retained as audit data, while current target labels
become UNKNOWN. A new PENDING_INPUT revision is registered transactionally.
Its quote notional policy is inherited so a corrected result remains comparable.
It cannot score or publish a negative until the new coverage generation verifies.
Long downtime never removes this work from the due queue.

Canonical rediscovery resets the scan cursor and creates windows from the new
creation anchor. An incoming replacement PairCreated also triggers this reset
before the old removed notice arrives. A never-scanned job is included even when
it has no receipt or active lease. Missing creation-block enrichment can later
make previously unscannable work runnable.

Both periodic reconciliation and realtime removed logs fence publication and
invalidate coverage. A new orphaned block hash is evidence even without a local
market/trade projection. Duplicate unknown removed notices do not repeatedly
create revisions. Raw/source invalidation, receipt invalidation and pending
replacements share the caller's database transaction; rollback restores them all.

## Manifests

The immutable entry manifest (`alpha-inputs-v1`) records:

- Pair identity, quote orientation, factory and creation block/hash/time.
- The exact entry trade IDs, fork identities, timestamps, wallets, sides, human
  amounts, raw amount metadata, decimals and trader resolution provenance.
- Coverage generation and full receipt boundaries/query scope/counts.
- Snapshot interval, price basis, exact entry VWAP, quote notional and policy ID.
- Score/label versions, a stable SHA-256 digest and separate capture timestamp.

Decimal values retain all stored digits. Digest serialization does not perform
Decimal arithmetic or round a 38-digit value to the default 28-digit context.
`digest_manifest()` reproduces the digest from the stored JSON after reload.

Future evaluation has a separate manifest referencing the entry digest. Later
prices cannot change the recorded entry inputs. The future manifest records the
actual observations used up to the evaluation cutoff, including partial coverage;
an observed positive is still excluded from complete-cohort rates until covered.

## Read and audit APIs

- Current candidates/statistics use usable current revisions. During rebuilding,
  an existing detail request can return PENDING_INPUT with a NULL score. The
  drawer shows “重建中” and the revision number.
- `GET /api/alpha/candidates/{token_or_pair}/revisions?limit=50` returns audit
  history with at most 200 items. Pass the returned `next_cursor` as `before_id`
  for stable descending-ID pagination. Invalidated/legacy rows are audit-only.
- `GET /api/alpha/revisions/{snapshot_id}/lineage` returns full entry/future input
  manifests and archived invalidated outcome. History/current DTOs contain small
  manifest summaries plus this URL; full source values are fetched on demand.

Audit history is not a trusted performance universe. Complete 1m cohort rates
continue using the versioned coverage/notional requirements and exclude invalid
revisions and pending replacements.

## Migration and remaining scope

0037 preserves old v1/v2 data and scored v3 values. Existing v3 scores without an
entry manifest become invalid history. It invalidates old receipt generations
once and schedules confirmed rescans: the previous direct removed-log path could
leave a stale receipt marked canonical, so old status alone cannot prove coverage.
Migration makes no provider calls. If archival RPC is unavailable, rebuilding
remains explicitly pending. Downgrade refuses to discard appended revisions.

Market/risk/intelligence/radar snapshots, early-buyer aggregates, Forward entry
observations and exported historical datasets still require their full lineage
and reconstruction work. This Alpha slice does not complete R3. Current due Alpha
inputs now use batched, window-scoped database reads. Dense-path manifest storage,
canonical-anchor retention, asynchronous reconstruction throughput and global
publication-lock latency remain R11 work. Full Compose restart/live-provider
acceptance remains R14.


The v4 exact-window arithmetic is a new recipe, not a revision of v3. Archived
v3 rows remain available as stored research; new v4 windows start at input revision0
with their own policy/manifests and denominator. Canonical invalidation marks
archived recipes untrusted too, preserving their original outcome in audit data.
Automatic reconstruction emits the active recipe; it does not claim to rerun an
unsupported archived algorithm under a new version label.

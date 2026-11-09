# Canonical anchor retention and audit

Apply Alembic `20261109_0046` before starting any updated application or worker.
The migration only adds the archive table and constraints; it does not rewrite
or remove existing hot anchors. Empty archives may be downgraded. Once an archive
contains evidence, online downgrade refuses to discard it; offline downgrade
cannot verify that requirement and also refuses. Stop old binaries before this
storage protocol is used; rolling/mixed-version deployment remains R12/R14 work.

Canonical anchors are safety evidence. Confirmed scans, including empty scans,
must still see a known conflicting fork after an anchor leaves the hot table.
There is no TTL deletion of hashes, coverage receipts, research input manifests,
or historical result revisions in this change.

The reconstruction worker also runs one anchor maintenance cycle in a separate
source transaction. Its own `anchor-archive` heartbeat is visible at
`GET /health` → `workers.anchor_archive.research`. This cohosted maintenance phase
does not add a separate Compose service or hold a canonical lock during heavy
research reconstruction. Busy canonical publication yields under a250ms per-lock
deadline and retries next cycle; errors expose fixed categories only.
Telemetry is awaited before taking the fence and after source commit/rollback,
never while retaining that global lock. COMMIT remains an in-memory phase and
can describe a failed commit; successful committed counts appear in the final
report. Slow heartbeat writes cannot extend either an archive or IDLE fence hold.

`CANONICAL_ANCHOR_HOT_BLOCKS` defaults to4096 and accepts512–10000000. Effective
retention is the larger of this value and `BSC_REORG_LOOKBACK_BLOCKS + 1`, keeping
the entire active reconciliation window hot. One operation compacts at most one
complete512-height bucket. After backlog drains, hot rows fit the effective window
plus at most511 incomplete-bucket heights. Undrained backlog can temporarily exceed
that bound; block-height windows are not wall-clock age cutoffs. Maintenance runs
after each rebuild cycle, so a slow reconstruction can delay compaction.

Each immutable segment stores chain/bucket identity, revision, exact observed
height/hash/UTC-time triples, row count, bounded compressed bytes and SHA256 of the
uncompressed deterministic JSON. Missing heights stay missing. Decoder limits
both compressed and uncompressed bytes, checks framing/checksum/identity and
rejects duplicates or invalid timestamps. Verification failure stops trusted
lookup; corrupt evidence is never interpreted as an absent fence.

Readers select the newest segment per requested bucket, then merge hot rows by
observation time. Old authoritative responses cannot replace a newer observation,
even when the newer value is archived. Deep corrections return to the hot table;
the next maintenance pass appends a new segment revision and retains the old one.
Normal candidate APIs and health responses do not carry compressed payloads.

Copy, validation, new segment and conditional hot deletion commit atomically under
the same canonical publication fence as reconciliation/collectors. Deletion matches
the original height/hash/time values. A concurrent unexpected mutation, exception,
rollback or cancellation cannot commit a partial copy/delete. ORM guards reject
editing or deleting retained segments; administrative raw SQL remains outside the
trusted application mutation protocol.
Canonical reconciliation rolls back validation/transaction failures before it
releases its process-local session owner. Thus corrupt archived evidence stays
untrusted without retaining a global lock that prevents unrelated publication.

Audit endpoints:

- `GET /api/research/anchor-segments?limit=50&before_id=...`: at most200 metadata
  rows, with compressed byte counts and a cursor; no payload read into the API.
- `GET /api/research/anchor-segments/{id}`: checksum/format-verified full content of
  one segment (at most512 observed heights). `latest_archive_revision` describes
  archive history only. A newer hot correction may exist; it does not certify a
  current chain header or replace provider/canonical reconciliation.

These archives reduce hot/index footprint and compress evidence. Total evidence
still grows with chain history and genuine corrections. PostgreSQL may reuse
vacuumed pages rather than immediately shrink allocated files. Backups/restores
must include hot and archive tables in the same consistent database snapshot.
Production storage sizing/backup/restart, dense provenance-manifest storage,
selective invalidation and whole-path throughput/latency remain open R11–R14
requirements; no fixed disk cap or production performance SLO is claimed here.

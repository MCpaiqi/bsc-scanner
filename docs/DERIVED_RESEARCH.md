# Canonical derived research revisions

Apply migrations through `20261106_0038` before starting updated services. This
batch covers market, risk, wallet intelligence and token intelligence snapshots;
existing values remain stored as history. R3 still has the scope listed below.

## Visibility and history

Canonical invalidation advances the BSC research epoch in the source transaction.
Producers capture it before reading facts. Ordinary ORM queries require the current
epoch and VALID status, including aliases/latest subqueries. Old in-flight output
cannot become current or supersede a newer winner. Invalidation updates a small
epoch record instead of traversing every derived row synchronously.

Same-time refreshes append revisions/predecessors. A per-identity database lock
makes identical concurrent publication converge. Numeric values hash identically
before/after reload. ORM writes cannot alter stored inputs/results or reactivate
superseded rows. Audit/rebuild selects explicitly opt into history. Direct risk
identity-map access validates persisted visibility with SQL.

Independent DexScreener observations keep their primary provider source and
original observation time. Known invalid/future parent pairs exclude child facts.

## Sources and policy

`derived-inputs-v1` manifests record exact source values/IDs, parameters, semantic
versions, digests and dependency snapshot IDs/digests. Token intelligence references
wallet manifests. Affected wallet profiles use their complete canonical history
across tokens at the original cutoff; future facts are excluded.

Canonical flags and JSON orphan markers both apply. Early-entry time comes from
canonical PairCreated at/before cutoff. Token's unversioned creation cache cannot
resurrect an orphan/future creation. Historically observed subjects whose last
trade disappears receive UNKNOWN/sample-zero replacements rather than old scores.

Market aggregates follow their pair identity. Partial USD coverage is DEGRADED
with known/total counts. A window without canonical inputs produces an UNAVAILABLE
audit revision, excluded from ordinary reads; it does not prove zero market volume.

Risk uses explicit resolved evidence and canonical trades. Unversioned Token cache
fields have no historical/fork identity and are excluded. Historical reconstruction
cannot reacquire missing external evidence: those dimensions remain UNKNOWN while
canonical behavior may still indicate risk.

Known parameters are preserved. Missing legacy intelligence parameters use an
explicit `legacy-defaults-v1` policy in the new manifest; old parameters are not
claimed recovered. Unsupported recipes/sources remain unavailable with an
UNSUPPORTED job. An old job cannot overwrite a fresh current-policy winner.

## Recovery and audit

The independent `research-rebuild` Compose worker uses PostgreSQL without Redis or
RPC. It repairs bounded cyclic cursor pages and evaluates at most 16 due jobs by
default. Retries, out-of-order IDs, restarts and additional epoch changes retain
work. A separate heartbeat task reports progress while reconstruction waits.

Read-only endpoints:

- `GET /api/research/snapshots/{table}/{id}`: full lineage/history with persisted
  current-validity status, explicitly audit-only.
- `GET /api/research/epochs?limit=50`: invalidation reasons with cursor.
- `GET /api/research/rebuilds?limit=50`: durable job status/result/error with cursor.

Lists allow at most 200 rows. Full source data is requested on demand; normal
wallet/token/risk payloads carry revision/epoch/digest and a lineage URL.

0038 quarantines pre-lineage chain-derived rows as epoch zero; cursor repair
reconstructs them. Primary DexScreener observations remain available. Migration
and reconstruction make no provider calls. Lossy downgrade is refused after
revisions, invalidations or rebuild work exist.

## Remaining programme work

R3 still covers EarlyBuyer/positions, radar cached features/signals, Evidence
Resolver dependencies, Forward entry observations and materialized datasets/
backtests. These artifacts are not validated by association with the four tables.
Supported source mutation must use canonical invalidation; raw SQL audit access
is separate from trusted current APIs.

Selective invalidation, dense-history streaming/retention and latency are R11 work.
Full health-dashboard integration, clean-checkout CI, deployment access and full
Compose restart/live-provider acceptance remain R11–R14 gates in the ledger.

# Canonical derived research revisions

Apply migrations through `20261106_0039` before starting updated services. This
covers market, risk, wallet/token intelligence, early-buyer and position snapshots;
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

## Early buyers and positions

Historical dataset recipe `dataset-v4`, Radar features/signals and the early-buyer
API replay the same canonical facts at a specified cutoff. Cached EarlyBuyer rows
cannot supply historical evidence. The adapter requires canonical Pair creation
at/before cutoff, applies flags/JSON/parent-pair guards, merges trade/market-only
observations by tx/log (trade projection wins), and excludes future events from
both calculation and its manifest. A fork changing during the read requires retry.

USD accounting uses TradeEvent.price_usd, explicit USD metadata or USD value /
normalized token amount. Raw MarketEvent amounts are normalized only from captured
event token_decimals; absent decimals leave quantity/position unknown. Current
Token cache decimals cannot supply historical unit evidence. A generic pool quote
is not a USD price. Missing quantities/costs remain unknown. Sales
reduce known inventory even when their USD proceeds are missing; FIFO follows
chain log order and uses a deque. Positions cover every observed trading wallet,
including wallets outside the configured early-buyer window.

Radar features/signals use an entry-only path: first 24 hours of BUY evidence,
at most 512 rows plus a sentinel from each source. This avoids loading lifetime
positions on each event. Saturated first-20 membership is known; an unsaturated
count with truncated evidence stays UNKNOWN with EARLY_ENTRY_SOURCE_LIMIT. Source
projection priority also applies when a Trade side corrects a Market side. Full
API/dataset/position replay still needs R11 dense-history streaming/retention work.

Source queries refresh retained ORM values before recording a current epoch;
independent writer corrections cannot be certified using old identity-map values.

Buyer/position snapshots preserve as_of, recipe/configuration hash, epoch and
immutable revisions. Losing the last canonical input publishes an UNAVAILABLE
audit revision; ordinary reads cannot return the old wallet. Existing audit and
durable research-rebuild endpoints/worker support both tables. Dictionary replay
imports stay unverified at epoch zero; trusted production reconstruction reads
stored canonical facts using `EarlyBuyerEngine.refresh`.

0039 preserves old cache payloads at epoch zero. Since old caches lack build times,
the reconstruction cutoff is explicitly first_buy_timestamp for buyers and
last_trade_timestamp for positions (1970 if absent), with legacy-defaults-v1
policy recorded. This does not recover the old cache's unknown build cutoff.
Old materialized datasets remain immutable artifacts; new canonical buyer
extraction has a distinct dataset-v4 config hash. They still require the artifact
staleness/rebuild work below.

## Remaining programme work

R3 still covers radar cached features/signals, Evidence
Resolver dependencies, Forward entry observations and materialized datasets/
backtests. These artifacts are not validated by association with these snapshots.
Supported source mutation must use canonical invalidation; raw SQL audit access
is separate from trusted current APIs.

Selective invalidation, dense-history streaming/retention and latency are R11 work.
Full health-dashboard integration, clean-checkout CI, deployment access and full
Compose restart/live-provider acceptance remain R11–R14 gates in the ledger.

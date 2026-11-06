# Canonical derived research revisions

Apply migrations through `20261106_0043` before starting updated services. This
covers market, risk, wallet/token intelligence, buyers/positions, Radar/Signal, historical datasets and Forward snapshots;
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
replays eligible immutable provider receipts at the original cutoff without network
calls. Missing/future external evidence remains UNKNOWN while canonical behavior
may still indicate risk. Provider field/unit compatibility with the risk recipe
remains an explicit programme gate (including is_honeypot support in risk-v1).

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

Historical dataset recipe `dataset-v5`, Radar features/signals and the early-buyer
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

Shared wallet replay manifests contain wallet replay parameters. Token admission
thresholds and early-buyer windows belong to token rollups; changing those settings
cannot create wallet revisions or invalidate unrelated token policies.

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

## Radar, Signal and chain evidence

RadarFeatureObservation freezes canonical discovery proofs, five-minute activity,
bounded early-entry sources, prerequisite snapshot IDs/digests, configuration,
original cutoff, feature values and priority. Candidate caches reference a snapshot.
Lists/details, Signal generation, audit scheduling, production rule evaluation and
new Forward observation writes verify that boundary. An invalidated cache displays
REBUILDING with unknown features/score while work recovers. Cached Token evidence,
launchpad or candidate smart/risk values cannot certify canonical research.

Signal generation uses those immutable features and explicit USD prices. Same-time
corrections append revisions; old scores remain available through the existing
research audit endpoint. Signal manifests include complete weights/threshold/
configuration, preserving even Decimal formatting needed by existing config hashes.
Normal payloads expose Radar and Signal lineage URLs. Discovery conditions and
parent/raw provenance are rechecked, while recent sell activity still refreshes
observation time without becoming discovery evidence.

Bounded durable reconstruction supports both tables. Invalidated prerequisites must
recover before dependent outputs finalize; unsupported/missing sources stay
unavailable. Jobs preserve original cutoff/configuration and cannot rewind a newer
live candidate. Rebuild status transitions are labelled RESEARCH_REBUILD at actual
processing time; the research observation retains its original as_of.

Supersession also invalidates dependents within the same canonical epoch. A durable
reverse dependency graph is populated from manifests; a recursive update marks
transitive outputs INVALIDATED in the source revision's transaction. Publication
holds shared locks on prerequisite rows, so a source correction cannot miss an
uncommitted dependency edge. Rollback restores both source and dependents. Cyclic
repair schedules same-epoch invalidations as well as older epochs. 0040 backfills
earlier dependency edges and quarantines those older dependent outputs once.

Current pointer reads compare against the persisted epoch inside the same SELECT.
Configured discovery thresholds are inherited/preserved across Signal and evidence
validation, and current audit runs use settings.radar. All evidence-level/source-type
aliases share resolver classification and require the same chain proof. Resolution
uses the latest applicable observation per provider/field rather than treating that
provider's own historical values as competing sources.

Delayed discovery still records sampled market baselines at original discovery
event time. The writer builds a projection at that cutoff, retaining T0-specific
source IDs/digest rather than using future processing-time features. Missing entry
prices remain UNKNOWN; this does not fabricate a tradable price at PairCreated.

0040 preserves old Radar feature/priority/state context and Signal values at epoch
zero. Legacy missing parameters use explicit legacy-defaults-v1 policy rather than
claiming the former configuration recovered. Lossy downgrade is refused after new
revisions or rebuild work. Raw unversioned imports are unavailable after migration.

Stored CHAIN observations require a current epoch, matching canonical source/proof,
subject, field/value and exact source/raw fingerprints. The supported proof format
certifies canonical discovery assertions; unreferenced historical RPC/contract
claims remain UNKNOWN. External primary provider observations retain provider
provenance, freshness and availability timestamps. Risk rechecks resolved chain
observations at its own epoch. Stored evidence is immutable. Live provider collection
uses completion time; explicit historical cutoffs never admit later responses.

## Historical datasets and backtests

`dataset-v5` reads the same explicit USD sources and deterministic 30-second entry
selector as live Forward: TradeEvent.price_usd, known TokenMarketSnapshot.price_usd,
or MarketEvent.price explicitly labelled USD. Generic pool quotes and unversioned
TokenScore caches cannot supply historical features. Activity scores replay
canonical five-minute buy/sell counts. Market cap/liquidity require explicitly USD
snapshot fields; absent units remain unknown. Missing prices do not remove losers
from the observed universe. Token launchpad labels alone are not discovery proofs.

The selected entry freezes the pair used for all future prices. Both adapters pass
stable source references to price-path-v2, including equal-time endpoint tie breaks.
24-hour extraction includes the shared five-second endpoint tolerance. The builder
records `evaluation_as_of` in each export and manifest: by default the earlier of
the build read time and period end + 24 hours + 5 seconds. Callers may select an
earlier cutoff. Snapshot as_of and observed_at must both be known by that cutoff;
entry features must already be available at the signal timestamp. Late receipts
can complete outcomes at a later evaluation time, without claiming they were known
earlier. Historical/live parity compares the same evaluation availability time.

Materialization captures the research epoch before the universe/source read and
checks throughout streaming. The manifest contains source values, price provenance,
early replay inputs, row-content digest, recipes, periods and snapshot dependencies.
Dataset labels identify a study; changed inputs append a research revision with a
new ID. Rows inherit current parent visibility and cannot be overwritten/deleted.
Trusted loaders refresh persisted JSON and return copies rather than shared cache.

0041 retains and quarantines legacy metadata/payloads. The durable worker preserves
known recipes, dates and evaluation_as_of, waits for prerequisite reconstruction,
and appends replacements without committing unrelated caller work. Identical
writers converge without duplicate rows. Unknown legacy parameters explicitly use
`legacy-upgrade-to-dataset-v5`; former extraction is not claimed reproduced.
Unsupported recipes remain unavailable. Lossy downgrade is refused after new work.

Normal backtest API/CLI paths reject stale IDs/labels and recheck after evaluation.
API rejection is 409 / REBUILDING with an audit URL. Current results carry revision,
epoch and digest; CLI JSON/CSV exports carry their source identity. External file
imports are labelled UNVERIFIED with a content digest, since they cannot establish
current canonical validity. Audit-only
`GET /api/research/datasets/{id}/rows?limit=50&before_id=...` returns preserved rows
in pages of at most 200; the existing snapshot endpoint supplies the full manifest.

## Forward observations and results

0042 preserves and quarantines pre-lineage observations/outcomes. Current records
use the same epoch, immutable revision, manifest, dependency and audit contract.
Old values are not rewritten. Outcomes reference both a stable observation_root_id
(the first recorded observation) and the actual input observation_id revision.
Same-epoch endpoint/provider correction invalidates affected results transitively;
canonical reorgs hide old entries/results immediately. Reconstruction preserves
T0, frozen Radar/Signal parameters, entry pair, and evaluation availability time.

The cohort policy is `recorded-transition-cohort-v1`: retain the class that was
actually first recorded, rather than selecting a new cohort using corrected or
future performance. Canonical inputs may be corrected independently of that
recorded decision. A withdrawn canonical discovery/signal becomes an UNAVAILABLE
audit observation. Unrecoverable legacy signal parameters/scores stay unknown,
under explicit `legacy-forward-upgrade-v1`; former strategy parameters are not
claimed reproduced. Unsupported evaluation recipes remain audit-only.

Entry prices share explicit USD sources and the 30-second bound with dataset-v5.
Market cap/liquidity require explicit USD snapshots of the same frozen entry pair;
another pool's cached liquidity cannot create false liquidity-failure/rug labels.
Exact normalized source/Pair values and derived snapshot IDs/digests are retained.
Missing entry or endpoint prices never reuse orphaned values or prove zero returns.

ForwardEvaluationJob holds mutable attempts/retry time/status/result pointer, one
job per observation revision/horizon/recipe. Outcome attempts publish COMPLETE or
UNKNOWN research revisions; UNKNOWN results are excluded from usable results and
the job remains PENDING. Missing T0 prices can be recovered after delayed ingestion
without moving T0. Publication of the replacement observation and its new horizon
jobs is atomic. The first-observation identity is rechecked under the database
identity lock so competing transitions cannot move T0.

Bounded cyclic repair initializes missing jobs, cancels obsolete input jobs and
reconciles completed jobs whose result was invalidated/rebuilt. COMPLETE→UNKNOWN
reopens retry; COMPLETE replacement fixes result_id. Repair uses job-first locks,
outside result publication, avoiding inverse lock order with evaluators. Busy repair
cursors are skipped so independent evaluators can claim due jobs. Per-job savepoints
keep one rejected result from aborting neighboring work; rollback/restart retain
queue and immutable history. Dense price windows and repeated UNKNOWN retention
still require R11 bounds; a bounded job count alone does not bound their source rows.

Performance reads matched current input/result revisions in one SELECT and fences
epoch changes. It exposes source/current epoch, REBUILDING/withheld state and per
horizon observed, pending, not-due and withdrawn counts. Available-only metrics
retain their sample_count denominator. Audit-only
`GET /api/research/forward-jobs?limit=50&before_id=...` pages job provenance;
existing snapshot audit URLs expose old observations/outcomes and their manifests.
Full UI presentation/pagination remains the R9 programme gate.

## Wallet/token USD input policy

0043 preserves old wallet/token values, allows genuine unknown count/flow fields
to remain SQL NULL, and quarantines prior recipes plus their transitive consumers
through the durable dependency graph. Unrelated risk/market recipes retain their
visibility. Repair appends replacements at original cutoffs with preserved admission
thresholds/window settings; unsupported input policies remain explicitly unavailable.
Downgrade refuses unknown flows, USD-v2 results or reconstruction work.

`canonical-wallet-usd-v2` and `canonical-token-usd-v2` independently version the
input/accounting policy. Score-v2 weights and the profile-v1 storage container remain;
old unsafe calculations are not reproduced by retaining those identifiers. Manifests
contain raw Trade/Pair values, normalized inputs, actual score inputs and published
wallet IDs/digests. Every refresh path reloads persisted source values before replay.
Epoch fences and exact publication-winner checks reject older computations; token
rollups use the actual published wallet score/confidence rather than a local profile
with a different source identity. Reader APIs/repositories refresh stored JSON.

Cash flow uses the dedicated normalized TradeEvent.value_usd. Native/quote amounts,
cached metadata usd_value, generic pool price and cached creation/outcome labels do
not supply wallet USD evidence. Marks use explicit or derivable USD inputs. Finite,
nonnegative values are required. Non-object auxiliary metadata is treated as absent
metadata without discarding otherwise usable normalized facts or crashing replay.
PairCreated evidence supplies timing; later token cache labels cannot move it.

Unknown costs/quantities/marks do not become measured zero ROI or losses. Profile
exports expose valued/unvalued counts and KNOWN/DEGRADED/UNKNOWN status; unavailable
financial measurements are null. Known USD inflow/outflow subtotals are computed
independently of inventory matching, so missing quantity does not erase known cash.
Scoring uses documented neutral arithmetic inputs for missing metrics, observed
subset statistics and valuation coverage in confidence; exact inputs are in lineage.
Wallet valuation uses the observed trade path; as_of denotes the source cutoff.
Full-chain history coverage and current-market valuation remain provider/acceptance
work, rather than inferred from a fresh build timestamp.

Token flow is null when the selected side has missing USD; net flow is null when
either side is incomplete. An observed zero remains zero. API serialization retains
nulls, ROI sorting puts unknowns last, and Radar maps unavailable fields to UNKNOWN
while recognizing healthy CHAIN_EVENTS output. Available count/score evidence is
retained when other measurements are missing.

## Remaining programme work

Wallet/token freshness and in-flight binding now have specific regressions. R3
still requires the final reader/mutation audit: an isolated reproduction found the
legacy POST /api/wallet/replay query can include removed/noncanonical TradeEvents.
Its canonical filters and adjacent raw-research readers remain required fixes.
Supported source mutation must use canonical invalidation; raw SQL audit access
is separate from trusted current APIs.

Selective invalidation, dense-history streaming/retention and latency are R11 work.
Full health-dashboard integration, clean-checkout CI, deployment access and full
Compose restart/live-provider acceptance remain R11–R14 gates in the ledger.

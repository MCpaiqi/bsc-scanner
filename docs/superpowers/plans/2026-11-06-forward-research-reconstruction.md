# Forward observation and outcome reconstruction

Continue the active BSC research reliability programme inline. Preserve local
untracked sources/data and use the existing research publication contract. Apply
meaningful RED→GREEN verification and one fresh read-only review, then a scoped
local commit. R3 and R6–R14 are not complete by association with this slice.

## Design

Observations and outcomes gain immutable research epoch/revision/manifests and
reverse dependencies. The observation identity remains the first recorded
candidate/class cohort and original event time. Rebuilds correct canonical inputs
without backdating later observations or silently selecting a more profitable
cohort. Record this cohort policy explicitly; unknown legacy signal parameters
remain unknown. Canonical discovery withdrawal yields an UNAVAILABLE audit record.

Separate ForwardEvaluationJob retry state from immutable LiveSignalOutcome values.
Jobs are per observation revision/horizon/recipe, enqueued atomically and repaired
in bounded cyclic pages. Outcomes use the first observation ID as a stable root
identity, referencing the actual input observation revision. Retry/completion never
overwrites published values. Same-epoch source replacement and epoch changes hide
stale entries and results immediately, with durable dependency-aware repair.

Preserve exact T0, frozen Radar/Signal policies, entry-pair selection, explicit USD
sources, evaluation availability cutoff and price-path-v2/forward-v2 arithmetic.
Manifest exact sources/dependencies. Rebuild legacy rows under an explicit upgrade
policy, preserving their original values separately; unsupported recipes remain
unavailable. Missing entry/endpoint evidence stays UNKNOWN and retryable.

Migration0042 preserves/quarantines legacy observations/outcomes, adds immutable
revisions and durable jobs, and refuses lossy downgrade. Performance reads only
current canonical observations/results and distinguishes pending/withdrawn data.
Audit endpoints retain old values and job provenance.

## Tasks

- [x] Reproduce stale observations/results, lost retry provenance and reorg failures.
- [x] Add schema/migration/visibility and separate durable retry jobs.
- [x] Implement exact-source immutable observation/outcome publication and recovery.
- [x] Verify original cutoff/policy, same-epoch dependencies and missing evidence.
- [x] Real PostgreSQL migration/restart/concurrency/rollback and full checks.
- [x] One read-only review/fix pass, evidence ledger and scoped commit.

## Evidence and remaining scope

Final backend667 passed/26 skipped/13 existing warnings; isolated PostgreSQL
comprehensive suite128 passed/4 existing warnings. Source/tests Ruff, whitespace,
Compose config, Alembic0042 head and offline SQL1339 lines passed. One read-only
reviewer reproduced stale first lookup moving T0 and COMPLETE→UNKNOWN stranding
retry; both have fixes/regressions. Root PostgreSQL tests also reproduced/fixed busy
repair-cursor serialization and cross-pair entry liquidity. Repair reconciles job
pointers using job-first locks instead of locking jobs during result publication.

Test schemas were verified zero before removing the labelled isolated database
container/volume. Preserve existing untracked sources/data and prior temporary files.
Local commit only; no production migration, deployment, push or live provider calls.
R3's invalid in-flight wallet/token binding audit and R6/R7/R9–R14 remain open in
RESEARCH_RELIABILITY.md; the overall objective is not achieved by this slice.

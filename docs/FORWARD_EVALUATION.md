# Forward evaluation policy

`forward-v2` replaces the unbounded endpoint/entry-relative-difference calculation.
Its pure calculation recipe, shared with historical backtests, is `price-path-v2`.
The runtime remains PAPER/SHADOW. These are observed research outcomes, not
executed trades or proof that every chain event in the window was observed.

## Price evidence and time

- New T0 observations read canonical USD evidence available at `observed_at`,
  with both evidence timestamps and snapshot `as_of` no later than T0. Entry
  evidence can be at most 30 seconds old. Missing evidence stays UNKNOWN.
- Price sources are explicit `TradeEvent.price_usd`, known
  `TokenMarketSnapshot.price_usd`, or a legacy `MarketEvent.price` explicitly
  marked `metadata_json.price_basis = USD`. A generic quote price is insufficient.
- Source pair/token identity, pair creation time and canonical/removal markers
  are checked. Entry pair identity is retained for the future path. USD facts
  are not relabelled as on-chain when they came from an external provider.
- Each horizon chooses the nearest finite positive sample within ±5 seconds of
  its target. Ties prefer the earlier timestamp, then stable source order
  (TradeEvent, snapshot, explicitly marked legacy market event). Only evidence
  available by the evaluation cutoff is eligible.
- The path includes the entry and ends at the selected endpoint. Initial losses
  count; peak drawdown is `price / running_peak - 1`. Stop labels measure decline
  against entry; the peak drawdown is reported separately. Extrema and rug labels
  describe observed samples, not an absence-of-risk or full-coverage guarantee.

The result records its version, actual endpoint time, signed endpoint offset,
tolerance, source/model/id/pair, exact decimal calculation strings, path count and
a SHA-256 digest of the ordered input samples. Backtest evaluations and summaries
also expose their calculation version. Performance reads respect their `as_of`
cutoff, including the result's evaluation time.

## Durable work and versions

New production observations and their eight horizon tasks are committed together.
Old/imported observations are repaired by a bounded cyclic cursor. IDs are not
assumed to follow commit order: PostgreSQL two-writer tests demonstrate that a
late lower-ID commit is eventually processed. New production work does not wait
for a historical repair sweep.

Each call seeds at most `batch_size` observations and evaluates at most that many
due outcomes (default 256, allowed 1–2000). Missing entry, unknown price basis or
missing bounded endpoint leaves the task PENDING, records a reason, and retries
after 60 seconds. The cursor and retry dates persist across worker restarts.
This bounds task count per iteration; bounding/streaming very dense price paths
and the other Alpha work remains part of R11.

Migration `20261105_0035` labels existing results `legacy-v1` and adds current
version identity/provenance/retry fields and the repair cursor. Return columns
use PostgreSQL NUMERIC without the old six-integer-digit cap, so a large finite
return cannot roll back unrelated due work. Current and legacy results coexist;
current performance statistics include only the current recipe.

Migration does not rewrite old returns, infer old observation units, or mutate
immutable T0 observations. An old observation with no trustworthy price basis
remains unavailable. Versioned reconstruction of such observations and transitive
invalidation after a reorg remain part of the broader R3 work.

Run the existing migration step before starting the updated workers:

```powershell
python -m alembic upgrade head
```

Downgrade is refused when coexisting/corrected results would lose version or
provenance information. Archive deliberately before attempting such a rollback;
the migration never deletes results to force a downgrade through.

## Verification

```powershell
python -m pytest tests/unit/test_forward_evaluation_integrity.py -q
```

Set `SMMS_RESEARCH_TEST_DATABASE_URL` explicitly to an isolated PostgreSQL database
to exercise the same research cases on actual PostgreSQL, including two independent
writer transactions. Each case owns a generated schema. Pure calculation cases
do not use a database; the PostgreSQL visibility case is skipped on SQLite.

Set `SMMS_MIGRATION_TEST_DATABASE_URL` explicitly to run:

```powershell
python -m pytest tests/integration/test_forward_policy_migration.py -q
```

That test upgrades historical migrations through 0034, seeds a real legacy result,
upgrades to 0035, checks preservation plus current-version coexistence, and verifies
that a lossy downgrade is refused. It creates and removes only its generated schema.

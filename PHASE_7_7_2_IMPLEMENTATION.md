# Phase 7.7.2: Historical Point-in-Time Dataset Builder

## Historical Dataset Architecture

`HistoricalDatasetBuilder` reads normalized PostgreSQL facts through a synchronous
SQLAlchemy `Session` (the async CLI calls it through `run_sync`). It constructs a
token universe, historical signal timestamps, market state, smart-money snapshot,
risk snapshot, early-buyer state, and future evaluation prices. Rows are exposed as
`PointInTimeBacktestRow` and can be streamed or materialized.

## Point-in-Time Guarantees

For signal timestamp `T`, market/trade facts and `EarlyBuyer.first_buy_timestamp`
must be `<= T`; `TokenSmartMoneySnapshot.as_of` and `TokenRiskSnapshot.as_of` must
also be `<= T`. Current Token fields, wallet performance, and future snapshots are
never used to backfill features. Future market prices are retained only in the
evaluation path and never affect feature construction.

## Universe Definition

The BSC universe includes discovered `Token` rows with creation/first-seen time at
or before the build end and a `Pair` created at or before that time. Outcomes are
not used for eligibility, so winners, flat tokens, losers, and rugs remain present.
Each row carries `discovery_timestamp` and `universe_reason`.

## Coverage

Every build returns `tokens_total`, `tokens_eligible`, `tokens_excluded`,
`rows_generated`, per-source coverage, and explicit reasons such as `NO_PRICE`,
`NO_MARKET_HISTORY`, `NO_SMART_MONEY_HISTORY`, and `NO_RISK_HISTORY`.

## Storage

`backtest_datasets` stores the period, chain, dataset version, config hash, report,
and source coverage. `backtest_dataset_rows` stores serialized point-in-time rows
with indexes for dataset/token and signal timestamp. The Alembic migration is
`20260912_0014`.

## CLI

```text
smms dataset build --from <iso> --to <iso> --chain bsc --dataset-version <version> [--output report.json]
smms backtest --dataset <dataset id or version>
```

`smms backtest --input rows.json` remains available for fixtures, debugging, and
tests only.

## API

Production endpoints read materialized datasets:

* `GET /api/backtest/datasets`
* `GET /api/backtest/datasets/{id}/summary`
* `GET /api/backtest/datasets/{id}/signals`

The legacy summary/signals endpoints use the newest materialized dataset and return
insufficient data when none exists; they no longer read an in-memory row constant.

## Performance

The builder preloads each token's event and snapshot series once per token and
supports `stream_build` for bounded downstream memory. The report records build
duration and rows/sec fields; query-count instrumentation is left to the database
observability layer.

## Known Limitations

Market history currently uses normalized `MarketEvent`/`TradeEvent` rows. Missing
historical market observations are reported as unavailable rather than synthesized.
The materialized payload is JSON for portability; a future phase may add native
typed columns for warehouse analytics. No paper-trading or live-execution behavior
is part of this phase.

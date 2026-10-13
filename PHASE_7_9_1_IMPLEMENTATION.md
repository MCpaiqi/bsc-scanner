# Phase 7.9.1 - Live Signal Shadow Outcome

## Scope

The platform now records forward observations independently from historical backtests. A first entry into `WATCH`, `INTERESTING`, `HIGH_SIGNAL`, or `REJECTED` creates one immutable `LiveSignalObservation`. The row freezes the T0 timestamp, signal class/score, price, market cap, liquidity, smart-money and risk snapshot references, feature/config versions, model version, and a JSON copy of the T0 state.

## Evaluation

`LiveSignalOutcome` creates idempotent rows for `30s`, `1m`, `5m`, `15m`, `30m`, `1h`, `4h`, and `24h`. `evaluate_due` is retry-safe: future horizons stay pending, missing prices remain `PENDING/MISSING_PRICE`, and completed outcomes are never recalculated. Outcomes include return, maximum return, maximum drawdown, 2x target, stop, rug and determinable liquidity failure.

## Baseline and API

Rejected observations use a deterministic SHA-256 10% sample. The performance endpoint includes `HIGH_SIGNAL`, `WATCH`, `REJECTED`, `INTERESTING`, and `MARKET_BASELINE` groups and returns `mode=FORWARD_OBSERVATION`. It is deliberately separate from the backtest endpoints and tables.

Endpoint: `GET /api/radar/live-performance`

Database migration: `20260912_0017_live_signal_shadow_outcome.py`.

## Guarantees

- T0 fields are copied on insert and are not updated by later radar refreshes.
- A unique `(candidate_id, signal_class)` key prevents duplicate observations on retries.
- A unique `(observation_id, horizon)` key prevents duplicate outcomes.
- Future market events are excluded from radar T0 feature calculation; outcome evaluation only reads events after T0.

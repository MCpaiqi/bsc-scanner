# Phase 7.7 — Signal Engine + Backtest

Phase 7.7 is a read-only research validation stage. It never places live or paper
trades. Signal generation is point-in-time: features are filtered to
observation.timestamp <= T; observations after T are used only by evaluation.

## Signal contract

TokenSignal stores token, timestamp, WATCH/INTERESTING/STRONG/HIGH_RISK, score,
confidence, Smart Money/market/risk components, entry price/market-cap/liquidity,
reasons, risk flags, data status and model_version. Weights are supplied by
SignalWeights and normalized at runtime.

## Backtest

evaluate_signal reports 30s, 1m, 5m, 15m, 30m, 1h, 4h and 24h outcomes, target/stop
hits, ATH/drawdown timing and rug rate. summarize reports sample size, win rate,
profit factor, median/mean/expectancy, max drawdown, and Sharpe/Sortino-like
values only when observations exist. Missing history returns INSUFFICIENT_DATA.

baseline_comparison compares the composite Smart Money strategy with a market-only
baseline. walk_forward creates chronological train/validation/test partitions
without tuning on test data.

## CLI/API

smms backtest --input rows.json --strategy smart-money --threshold 50 --capital 1000

The command emits JSON and optionally writes --output JSON and --csv evaluation
rows. Read-only API endpoints are GET /api/backtest/summary and
GET /api/backtest/signals.

No synthetic performance is generated. A run with no eligible entry price or future
observations is explicitly marked INSUFFICIENT_DATA.

## Phase 7.7.1 correction notice

The original Phase 7.7 implementation mixed horizon observations as independent
samples, accepted insufficient horizon coverage, used worst terminal return as
drawdown, and did not isolate the market-only feature set. Those semantics are
superseded by `PHASE_7_7_1_IMPLEMENTATION.md`; prior backtest summaries must not be
used as evidence of strategy effectiveness.

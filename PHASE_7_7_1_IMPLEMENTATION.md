# Phase 7.7.1 - Backtest Correctness and Statistical Integrity

This phase hardens the read-only signal and backtest implementation. It does not
add paper trading, live trading, optimization, or a historical dataset builder.

## Correctness changes

- Composite, market-only, early-buyer-only and deterministic random/universe
  baselines have independent feature sets. Market-only cannot consume wallet,
  Smart Money, Early Buyer or risk-derived intelligence.
- Results are summarized independently per horizon. `unique_signal_count` is the
  strategy sample count; eight horizon observations are not eight trades.
- A horizon is available only when an observation is within its configurable
  target-time tolerance. Earlier observations never fill longer horizons.
- Drawdown is rolling-peak-to-trough on each future price path. MAE and MFE are
  reported separately.
- Market, Smart Money and risk snapshots dated after signal time fail closed.
  Early buyers and entry prices are filtered at or before signal time.
- UNKNOWN risk supplies no positive risk evidence, lowers confidence, records
  `risk_evidence_insufficient`, and produces at least DEGRADED data status.
- Signal classification is separate from entry eligibility. HIGH_RISK and WATCH
  are research classifications and are excluded from default strategy returns.
- Entry observations expose their age and are rejected as `STALE_ENTRY_PRICE`
  when older than the configured maximum.
- Signals carry `model_version`, `config_version` and deterministic `config_hash`.
- Chronological 60/20/20 remains available, with expanding or rolling fold
  boundaries that preserve train/validation/test isolation.

## API summary contract

Summary output contains `unique_signal_count`, `per_horizon_metrics`, `strategy`,
`model_version`, and `data_status`. Each horizon contains sample size, win rate,
median and mean return, profit factor, MFE, MAE, max drawdown, rug rate, and
target hit rates.

## Statistical warning

These corrections invalidate direct comparison with Phase 7.7 aggregate results.
No strategy effectiveness claim is made from current backtest output.

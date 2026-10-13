# Phase 7.5: Smart Money Scoring

## Delivered

- Extended the existing weighted `SmartMoneyMetrics` score to a stable 0-100
  contract with normalized feature values, signed `contributions`, and a clear
  explanation. Existing weights and fields remain compatible.
- Added independent confidence based on sample size, data completeness,
  historical coverage, and trade quality. A high score does not imply high
  confidence; one-token wallets remain `LOW` confidence.
- Added configurable half-life decay through `exponential_time_decay` and the
  `age_days`/`half_life_days` arguments. Median ROI, loser/rug observations, and
  consistency remain part of the score to reduce single-outlier and
  survivorship bias.
- Added `cluster_id` and correlation indicators to wallet behavioral metadata.
  Token aggregation counts one wallet per cluster to avoid treating coordinated
  wallets as independent smart money.
- Added `TokenSmartMoneySummary` and `aggregate_token_smart_money` with wallet
  count, buy/sell/net flow, top wallets, early smart buys, score, and `UNKNOWN`
  status when history is unavailable.
- Radar token responses and demo cards expose Smart Money Score, Smart Wallet
  Count, Smart Money Buy, and Early Smart Buyers. Wallet links point to the
  existing profile endpoint; unavailable values render `UNKNOWN`, never 0.

## API

- `GET /api/radar/tokens/{token}/smart-money`
- `GET /api/bsc/tokens` now enriches each token with smart-money summary fields.
- `GET /api/wallet/{address}/profile` now includes `confidence`, normalized
  features, signed components, and score explanation.

## Safety

No trade execution paths were changed. Trading remains paper-first and live
execution remains disabled by existing safety settings.

## Verification

```text
pytest -q
ruff check src tests
python -m compileall src
```

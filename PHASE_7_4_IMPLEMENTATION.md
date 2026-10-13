# Phase 7.4: Wallet Intelligence

## Delivered

- Added `WalletProfile` and `build_wallet_profile` in `src/smms/smart_money/profile.py`.
- Profiles can be built from canonical `TradeEvent` rows or persisted
  `WalletTokenPerformance` snapshots. Raw trades are replayed per token with
  Decimal weighted-cost accounting and preserve open and losing positions.
- Added aggregate invested/returned capital, realized and unrealized PnL, ROI,
  median ROI/multiple, best/worst multiple, wins/losses, average win/loss,
  profit factor, max drawdown, trade count, concentration, average position
  size, liquidity dependency, and 1x/2x/5x/10x/20x/50x/100x hit counts.
- Entry timing uses token creation, liquidity-added, or price-discovery
  timestamps when those facts are present. `WalletProfileReplay` supports an
  `as_of` cutoff for deterministic historical replay without look-ahead.
- Sample confidence is `LOW` for fewer than 4 token samples, `MEDIUM` for 4-9,
  and `HIGH` for 10 or more. Confidence is separate from the existing score.
- Added behavioral indicators for frequency, repeated buy size, same-block
  activity, immediate buy/sell behavior, and synchronized activity. These are
  evidence fields; they are not bot or wash-trading classifications.
- Existing `scoring.py` remains unchanged. API responses expose its score as a
  derived `score`/`smart_money_score` value for filtering and ranking.

## API

`GET /api/wallet/{address}/profile?chain=bsc&as_of=<ISO-8601>` returns one
complete profile. Unknown wallets return `404`.

`GET /api/radar/smart-money/wallets` supports `chain`, `sort` (`score`, `roi`,
`win_rate`, `sample_size`, `profit_factor`), `min_sample`, `min_score`, `limit`,
and `as_of`.

## Data boundary

Canonical `TradeEvent` facts are authoritative. Values that cannot be derived
from observed token/quote amounts remain unknown rather than being fabricated.
Historical replay filters both trades and per-token snapshots by `as_of`; no
future token outcome is queried while constructing a profile.

## Verification

Run:

```text
pytest -q
ruff check src tests
python -m compileall src
```

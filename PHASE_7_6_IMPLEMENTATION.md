# Phase 7.6 Implementation: Risk Engine

## Scope

Phase 7.6 adds a read-only Risk Intelligence chain independent from Smart Money. A high Smart Money score never downgrades a risk result.

The engine consumes only canonical project data (Token, TradeEvent, and existing metadata/holder evidence). It does not introduce trading, wallet signing, private-key handling, recommendations, or unverified external data.

## Risk Dimensions

- Contract Risk checks code existence, ownership, proxy/upgradeability, minting, blacklist/whitelist, pause/trading/transfer restrictions, limits, taxes, and external calls when facts are explicitly available.
- Liquidity Risk checks current/initial liquidity, liquidity-to-market-cap, liquidity change, LP concentration, removal signals, and recorded lock evidence.
- Holder Risk computes top holder/top-five/top-ten concentration while excluding LP, burn, router, exchange, and protocol addresses.
- Behavior Risk derives concentration, repeated-size, high-frequency, circular-trading, and synchronization heuristics from existing trades.

Every dimension supports LOW, MEDIUM, HIGH, CRITICAL, and UNKNOWN. Missing evidence remains UNKNOWN; it is never converted to SAFE or LOW. Bot and wash values are probabilities with an evidence list, not certainty claims.

## Snapshot and API

TokenRiskSnapshot is versioned by chain, token_address, as_of, and risk_version. The materializer is available through:

    smms risk refresh --chain bsc --risk-version risk-v1

Read-only endpoints:

- GET /api/radar/tokens/{token}/risk
- GET /api/radar/risk/tokens

Radar token cards display contract, liquidity, holder, behavior, bot, wash, and overall risk fields. The evidence disclosure is expandable per token.

## Verification

- pytest -q --basetemp .phase76-tmp: 117 passed
- ruff check src tests: passed
- python -m compileall -q src: passed

The default Windows pytest temp root is permission-restricted in this workspace; the passing run uses the repository-local .phase76-tmp base directory.

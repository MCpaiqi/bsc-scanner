# Phase 7.8.6 — Real-Time Trader Workstation

## Delivered

- Added an independent React + TypeScript + Vite application under `web/`.
- Replaced the card-oriented frontend with a dense canonical radar table containing token, age, launchpad, market cap, liquidity, 1m/5m volume, buy/sell activity, unique buyers, smart-money score/wallets/net flow, risk, signal, priority, and evidence status.
- Added `LIVE`, `HIGH SIGNAL`, `WATCH`, and `HIGH RISK` tabs plus search and deterministic client-side sorting.
- Added an on-demand token detail drawer with market, smart-money, risk, evidence, signal, audit-history navigation labels, and verified early-buyer rows.
- Added SSE consumption from `/api/radar/events`. Candidate discovery/update events merge into only the matching row; non-candidate events update the event stream without reloading the table.
- Added visible source health for BSC WS, HTTP fallback, DB, Redis, DexScreener, and GoPlus with `HEALTHY`, `DEGRADED`, `STALE`, and `DOWN` states.
- Empty, loading, error, stale, and reconnecting states are explicit. No mock tokens, random prices, or synthetic smart-money/risk values are generated.

## API mapping

The app consumes the existing FastAPI contracts: `/api/radar/candidates`, `/api/radar/candidates/{chain}/{token}`, `/api/radar/tokens/{token}/early-buyers`, `/api/radar/tokens/{token}/smart-money`, `/api/radar/tokens/{token}/risk`, `/api/radar/events`, `/health`, `/api/radar/health`, and `/api/bsc/stream/status`.

## Verification

From `web/`:

```bash
npm run typecheck
npm run build
npm test
```

The reducer tests cover matching-row updates and feature preservation for SSE candidate updates.

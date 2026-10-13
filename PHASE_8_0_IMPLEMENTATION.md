# Phase 8.0 Wallet Intelligence

Phase 8 upgrades wallet score reads into a deterministic Wallet Intelligence
Graph. `src/smms/smart_money/intelligence.py` is the shared deep module for
graph entities, explainable relationships, connected-component clustering,
four-part Smart Money Alpha scoring, bounded token flow windows, and historical
winner replay. It accepts normalized trade events and applies `timestamp <=
as_of` before deriving any result.

The wallet profile now includes entry timing, average holding period, exit
efficiency, realized/unrealized ROI, win/loss streaks and chain activity while
retaining all Phase 7 fields and snapshot compatibility.

New read surfaces:

- `GET /api/wallet/{address}/clusters`
- `GET /api/wallet/{address}/graph`
- `GET /api/token/{address}/smart-money-flow?window=1m|5m|15m|1h|24h`
- `POST /api/wallet/replay`

The React workstation exposes the Wallet Intelligence page at `/?view=wallet`
and links to it from the Radar header. Tests cover cluster correctness, Alpha
score explainability, cutoff/no-future-data behavior, bounded flow and replay.

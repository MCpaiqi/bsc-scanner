# Phase 7.1: BSC Real-Time Scanner Infrastructure

## Architecture

```text
BSC WebSocket (eth_subscribe logs/newHeads)
          or explicit HTTP polling fallback
        -> normalized ChainLog / Block
        -> BSCRealtimeIngestionService
        -> raw_chain_events (replayable, idempotent)
        -> stream_checkpoints (restart cursor)
        -> later launchpad normalization (Phase 7.2+)
```

The chain layer exposes provider-independent `BlockStream`, `LogStream`, and `ChainStream` protocols. Flap decoding remains isolated in `launchpads/flap`; DexScreener radar is unchanged. No signing, wallet, live trading, or smart-money scoring is used.

## Configuration

Environment variables are documented in `.env.example`:

- `BSC_RPC_URL` / `BSC_RPC_URLS`: read-only HTTP JSON-RPC endpoints.
- `BSC_WS_URL`: WebSocket endpoint. When set, the stream uses `eth_subscribe`.
- `BSC_WS_CONNECT_TIMEOUT_SECONDS`, `BSC_WS_RECONNECT_MAX_RETRIES`, `BSC_WS_BACKOFF_SECONDS`, `BSC_WS_BACKOFF_MAX_SECONDS`, `BSC_WS_JITTER_SECONDS`, `BSC_WS_HEARTBEAT_SECONDS`.
- `BSC_CONFIRMATION_DEPTH`: blocks required before `pending` becomes `confirmed`.
- `BSC_POLL_INTERVAL_SECONDS`: interval for the explicit HTTP fallback.

The HTTP fallback is reported as provider `bsc_http_polling`; it is never presented as a WebSocket stream. All RPC access is read-only and chain id defaults to BSC mainnet `56`.

## Storage Schema

`raw_chain_events` stores `chain_id`, block/transaction/log identity, block hash, contract address, topics, data, timestamps, provider, `pending|confirmed|orphaned` status, `removed`, and the original payload. A unique constraint on `(chain_id, transaction_hash, log_index)` makes ingestion idempotent and replayable.

`stream_checkpoints` stores provider, stream name, last processed block/log/transaction, status, and `updated_at`. Checkpoint updates are monotonic within a block and are committed after event persistence.

## Recovery and Reorg Behavior

On restart, the service reads its checkpoint before marking the stream running. WebSocket subscriptions are recreated after disconnects with exponential backoff and jitter; subscription filters are sent again. Duplicate logs are suppressed in memory and by the database identity constraint. Provider `removed=true` notifications are retained and marked `orphaned`. Pending logs are promoted only below the configured confirmation depth, so a recent reorg remains representable.

## Health and CLI

- `GET /health` includes `bsc_stream` readiness.
- `GET /api/bsc/stream/status` returns provider, current/processed block, lag, event count, reconnects, provider errors, latency, and checkpoint status.
- `smms bsc stream status` reports configured transports.
- `smms bsc stream checkpoint` reads the persisted status.
- `smms bsc stream start` runs the durable stream until shutdown.

## Known Limitations

- `newHeads` is normalized for direct consumers, but confirmation advancement is currently driven by processed logs; deployments that need block-only confirmation should run a block coordinator.
- Reorg detection beyond provider `removed` notifications requires retaining and comparing canonical block headers; the raw schema keeps block hashes for that follow-up.
- No automatic event decoding or launchpad-specific normalization is performed in this phase.
- Real RPC integration tests remain optional; unit tests use local fake transports and SQLite.

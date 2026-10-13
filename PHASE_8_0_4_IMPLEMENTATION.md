# Phase 8.0.4 - Live BSC End-to-End Closure

Phase 8.0.4 keeps production BSC-only (`chain_id=56`), paper-only trading, and
fail-closed evidence semantics.  The runtime path is now:

```text
Verified Factory -> PairCreated -> ActivePairRegistry -> dynamic WS/HTTP pair batches
-> Swap/Sync -> transaction.from resolver (bounded cache) -> canonical TradeEvent
-> strict USD valuation -> TokenMarketSnapshot -> Wallet/Early Buyer/Smart Money
-> Radar -> Redis fanout (best effort) -> SSE -> Chinese UI
```

`ActivePairRegistry` bounds active subscriptions with
`BSC_MAX_ACTIVE_PAIRS`, `BSC_PAIR_ACTIVITY_TTL_SECONDS`, and
`BSC_PAIR_SUBSCRIPTION_BATCH_SIZE`.  Expiration evicts only live subscriptions;
historical `Pair`/raw events remain durable.  HTTP fallback uses the same pair
address and event-topic filters.

Swap trader identity uses `eth_getTransactionByHash`: `transaction.from` is
the economic caller, while `swap_sender`, `swap_recipient`, `transaction_to`,
resolution, and confidence remain audit fields.  Router/factory/pair addresses
are excluded from normal wallet attribution.

Trade units are explicit (`token_amount`, `quote_amount`, `native_amount`,
`price_quote`, `value_quote`, `price_usd`, `value_usd`).  Missing USD pricing is
`NULL/NO_USD_PRICE`; native or quote amounts are never used as USD.  Snapshot
volume aggregates only known USD values and records coverage metadata.

Heartbeat and DexScreener enrichment run as independent background tasks.  API
stream status reads shared `runtime_worker_status`; Redis health and SSE
connection health remain separate.  Reorg identity includes block hash and all
business projections require canonical, non-removed events.

Migrations: `0023`, `0024`, `0025`, `0026`, `0027`.  Live BSC smoke testing is
`NOT RUN` until the user supplies verified BSC RPC/WS, factory, PostgreSQL, and
optional Redis settings.

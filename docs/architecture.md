# 架构说明

Phase 0 建立配置、日志、依赖边界和健康检查；Phase 1 增加关系型数据库结构与仓储；Phase 2 增加数据提供器边界和标准化层：提供器返回权威原始数据，标准化层生成规范记录但不改写来源。Phase 4 加入链无关的链/发射平台接口、只读 BSC 提供器和“先验证后启用”的 Flap 适配器。Phase 6 增加有限范围的 BSC RPC、事件验证元数据、幂等事件仓储，以及单 Token 历史摄取流程。

所有外部服务必须通过提供器适配器访问。缺少凭据时，健康检查明确返回 `not_configured`；系统不会制造数据。

BSC 适配器只调用有文档依据的 JSON-RPC 方法。生产 runtime 仅运行 BNB Smart Chain（Chain ID 56）；历史 Solana 数据只保留 provenance，不再作为并行生产提供器。Flap 特有的事件选择器和曲线读取器位于 `launchpads/flap`。已经确认的选择器可以写入配置，但在事件发出者地址和真实 fixture 完整前，发射平台仍保持禁用。DEX swap 解码属于后续数据增强，不能把任意 Token 转账推断成交易。

历史数据路径严格分层：

```text
BSC JSON-RPC
  -> Flap 适配器 / 解码器
  -> 标准化 Token、TradeEvent、TokenLifecycleEvent
  -> 原始链上证据 + 幂等仓储
  -> PostgreSQL + 时间点一致的钱包/Token 计算
```

实时 BSC WebSocket/HTTP polling 摄取、raw event 持久化、checkpoint 恢复和确认状态已在 Phase 7.1 实现；实盘执行仍不在当前范围内。

## Phase 7.7.3 — Historical observability

HistoricalDatasetBuilder derives event-time lifecycle timelines from existing Token,
TokenLifecycleEvent, launchpad TradeEvent and Pair facts. No schema migration is required.
LAUNCHPAD_DISCOVERED, LAUNCHPAD_ACTIVE, DEX_PAIR_DISCOVERED and MIGRATED_TO_DEX
are derived using only facts at or before the row timestamp. Migration requires an explicit
migration event; pair creation alone does not prove graduation. Later launchpad trades do
not regress a DEX/migration state. Unknown pair creation times fail closed. Lifecycle
transitions produce rows even without trades; rows never predate market discovery.
Current Token status and outcome are not used for eligibility.

This replays public chain event-time observability, not local ingestion latency. Creation
timestamps represent public chain facts; first_seen_at is the fallback for launchpad discovery.
Launchpad attribution must describe the creation source. The existing schema does not
provide a bitemporal ingestion ledger.

Dataset v2 exports feature_price_history (timestamp <= signal_timestamp) and
evaluation_price_path (signal_timestamp < timestamp <= signal_timestamp + 24h).
The backtest boundary constructs a validated FeaturePriceHistory capability before signal
generation; only outcome evaluation receives the evaluation path. Legacy mixed prices are
filtered at the boundary and again inside generate_signal. New exports omit legacy price
fields. Rebuild old datasets to correct historical universe membership.

Release audit is a read-only recursive tree check that fails for unsafe artifacts, possible
credentials, unreadable paths, symlinks and uninspected archives, without printing content.
Shared test scripts install editable dev dependencies, explicitly load pytest-asyncio, use
isolated temporary storage, run pytest and ruff, and disable live trading.

## Phase 7.9.3 - Production Radar observability

Production observability is a separate runtime module (`smms.observability`). A
single source-health registry covers BSC WebSocket, BSC HTTP, PostgreSQL, Redis,
DexScreener, GoPlus, Audit Queue, and SSE. It classifies sources as
`HEALTHY`, `DEGRADED`, `STALE`, `DOWN`, or `UNKNOWN` and records bounded latency,
error, 429, cache, and freshness metadata. The metrics registry exposes chain
head/processed block/lag, ingestion and decode rates, candidate discovery, audit
backlog and latency, provider rates, and SSE client/event rates.

The health and diagnostics endpoints expose only redacted operational metadata.
Important radar data blocks carry `observed_at`, `updated_at`, and `freshness`;
missing timestamps are `UNKNOWN`, and stale cache responses are labeled `STALE`.
External provider calls use a circuit breaker and bounded stale-cache fallback.
BSC WebSocket can fail over to HTTP polling; chain ingestion, Smart Money, and
Risk Engine remain independent of third-party evidence outages. PostgreSQL
remains canonical and Redis/SSE delivery is best-effort. Trading remains disabled.

## Phase 7.8.1 — Canonical radar

The live radar universe is selected from SMMS-owned chain facts rather than an
external token list:

[91m[0m```text
PairCreated / launch lifecycle / first meaningful BUY / market activity
  -> RadarCandidate (canonical Token reference + evidence/history)
  -> bounded fast features (UNKNOWN when unavailable)
  -> explainable Radar Priority Score
  -> aggregate radar API
[91m[0m```

Candidate evidence is idempotent and tied to raw-chain provenance when available.
Removed or orphaned source events trigger fail-closed revalidation, so a reorg
cannot leave an unsupported candidate visible. External sources can enrich facts
but cannot select the candidate universe. Audit Queue scheduling is intentionally
deferred to a later phase.

## Phase 8.0.1 - BSC Wallet Intelligence hardening

Wallet Intelligence runs on BSC only. Snapshot, graph, flow, profile, and replay reads are point-in-time and bounded. The graph builder aggregates wallet-network relationships, uses temporal co-buy windows, caps nodes/edges/events and returns truncation metadata. Cluster IDs are deterministic hashes of canonical chain/member/version input.

The BSC realtime path is provider-independent: WebSocket retry and endpoint rotation feed a checkpointed raw-event stream; HTTP polling is a real fallback only after chain/capability checks; recovered WebSocket endpoints are probed, catch up from the checkpoint, and fail back with hysteresis. Raw and normalized events remain idempotent by transaction/log identity. External evidence is secondary and failure-safe: DexScreener uses bounded timeout/rate/cache settings, GoPlus uses Bearer access tokens, and unavailable evidence remains UNKNOWN. Flap remains disabled with VERIFY_ON_CHAIN until real evidence is verified.
Phase 8.0.3 preserves BSC-only operation and records market provenance (`ON_CHAIN_TRADE_AGGREGATION` vs `DEXSCREENER`). Runtime status reads shared worker heartbeat/checkpoint state; SSE connection health is distinct from Redis event-fanout health.

## Phase 8.0.4 - Live BSC end-to-end closure

The production path is a bounded, fail-closed chain:

```text
verified factory PairCreated
  -> ActivePairRegistry (TTL/priority/cap)
  -> dynamic WS or bounded HTTP pair batches (Swap/Sync/Mint/Burn)
  -> shared transaction.from resolver (TTL/LRU)
  -> canonical fork-aware TradeEvent with explicit units
  -> strict USD valuation (NULL when unavailable)
  -> TokenMarketSnapshot -> Wallet/Early Buyer/Smart Money
  -> Radar -> best-effort Redis fanout -> SSE -> Chinese UI
```

Runtime settings `BSC_MAX_ACTIVE_PAIRS`, `BSC_PAIR_ACTIVITY_TTL_SECONDS`, and
`BSC_PAIR_SUBSCRIPTION_BATCH_SIZE` bound live work. Pair expiration never deletes
historical rows. DexScreener enrichment runs independently in the BSC ingestion
worker and only updates a token/pair already discovered from a canonical on-chain
PairCreated event. Missing RPC, factory, database, or Redis configuration remains
`DEGRADED`/`UNKNOWN`; no fallback value is fabricated.

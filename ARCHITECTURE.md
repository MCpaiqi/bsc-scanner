# 系统架构

SMMS 当前是 BNB Smart Chain（BSC，Chain ID 56）上的聪明钱 Meme 情报引擎。

```text
链数据提供器（ChainProvider）
    -> 发射平台适配器
        -> Token / TradeEvent / TokenLifecycleEvent
            -> 研究、评分、信号、回测、模拟执行
```

BSC 提供器负责有限范围的 JSON-RPC 传输、重试与故障转移、区块时间戳，以及区块、交易、回执和日志的标准化。Flap 适配器负责经过验证的选择器和解码逻辑；这些细节不会泄漏到核心标准化或聪明钱模块中。历史摄取流程会先保存原始证据，再写入规范事件记录。

当前主要目标：BNB Smart Chain 上的 Flap。

当前生产 runtime 仅使用 BSC；其他链代码只保留历史兼容性，不参与生产摄取。

安全边界：当前阶段不实现实盘交易。未知的 Flap 链上事实必须作为 `VERIFY_ON_CHAIN` 配置缺口保留，不能用猜测值代替。在创建/交易事件发出者角色和真实 BUY/SELL fixture 完成验证前，仓库内 Flap 配置保持禁用。

Phase 7.9.3 在链摄取、慢智能、Radar State 和 FastAPI/SSE 旁增加独立的
Production Observability 层。Source Health Registry 统一回答 BSC WS/HTTP、
PostgreSQL、Redis、DexScreener、GoPlus、Audit Queue 与 SSE 状态；Metrics Registry
记录 chain head、processed block、lag、ingestion/decode、candidate、audit、provider
与 SSE 指标。所有重要 Radar 数据块带 `observed_at`、`updated_at`、`freshness`，
stale 数据不会在工作台显示为实时。第三方 Provider 采用 stale-cache、backoff、
circuit breaker 和 BSC HTTP fallback，故障不传播到 Chain、Smart Money 或 Risk
Engine；交易仍保持禁用。

Phase 7.9.4 的生产闭环为：

```text
BSC WS/HTTP -> canonical chain events -> RadarCandidate
  -> RadarFeatureSnapshot -> priority audit queue
  -> incremental intelligence -> external evidence orchestrator
  -> EvidenceResolver -> RiskSnapshot -> LiveSignalService -> Signal
  -> Radar state diff -> Redis/SSE, alerts, forward observation
  -> React Trader Workstation
```

链上事实始终优先于 provider evidence；所有 Signal、Risk 和 Forward 结果按
`as_of` 做 point-in-time 截止。DexScreener 不参与 Radar universe discovery。
## Phase 8.0.4 live BSC data path

BSC raw logs are filtered to verified factory PairCreated and bounded active-pair
topics. PairCreated populates ActivePairRegistry, which dynamically subscribes to
Swap/Sync/Mint/Burn over WS or bounded HTTP polling. Decoded swaps resolve the
transaction caller through a shared cached `eth_getTransactionByHash` context,
then become fork-aware canonical TradeEvent rows with explicit token/quote/USD
units. Canonical trades feed TokenMarketSnapshot, Wallet Intelligence, Early Buyer,
Smart Money, Risk, Radar and Redis/SSE. DexScreener runs as background enrichment
for already discovered BSC pairs only; it cannot select the Radar universe.

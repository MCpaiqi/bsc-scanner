# Phase 8.0.3 — Live BSC Data Wiring & Market Intelligence Repair

## 实际数据流

此前 BSC raw log 经协议适配器只落入 `MarketEvent`；普通 PancakeSwap Swap 没有进入 `TradeEvent`，DexScreener 只作为 EvidenceObservation，Radar 也会把整个 feature 集合退化为 UNKNOWN。运行状态 API 还会临时创建 ingestion service，不能代表真实 worker。

## 修复后目标数据流

```text
BSC Raw Log
  → Verified Factory / bounded Pair subscriptions
  → PancakeSwap ABI decoder (canonical keccak topics)
  → MarketEvent + Canonical TradeEvent
  → 1m/5m aggregation
  → TokenMarketSnapshot (on-chain or DEXSCREENER provenance)
  → Wallet / Early Buyer / Smart Money
  → External Evidence / RiskInput
  → Signal / Radar Feature
  → Redis fanout / SSE / 中文雷达
```

### 本阶段完成

- 删除 Swap、Sync、Mint、Burn 的占位 topic，运行时根据 canonical event signature 生成并校验 topic0。
- 默认订阅不再允许空 logs filter；无显式过滤时只监听 PairCreated topic。
- BSC Swap 在确认 pair orientation 后生成带 BUY/SELL、pair/base token、decimals、block identity 和 provenance 的 TradeEvent。
- 新增 `TokenMarketSnapshot` 与 migration，DexScreener 返回物化到快照，Radar API 可读取快照字段。
- Radar features 增加独立的 `volume_1m`、`volume_5m` 与 `reason_codes`，UNKNOWN 不再覆盖已知市场字段。
- Risk Engine 引入 `RiskInput`，ORM Token 与外部证据统一合并。
- `/api/bsc/stream/status` 读取持久化 checkpoint/worker heartbeat，不再 new ingestion service。
- 新增 `smms diagnostics live-bsc` 只读诊断命令，不打印完整 RPC/WS secret URL。

### UNKNOWN reason semantics

`NO_SWAP_DATA`、`NO_MARKET_DATA`、`NO_PRICE`、`NO_SMART_MONEY_SAMPLE`、`NO_WALLET_TRADES`、`TOKEN_DECIMALS_UNKNOWN`、`GOPLUS_DISABLED`、`NO_LAUNCHPAD_EVIDENCE`、`STALE_DATA`、`UNVERIFIED_CONTRACT` 均表示缺少证据，不用默认数字伪造。

Flap 仍保持 `FLAP_NOT_VERIFIED`；普通 PancakeSwap Pair 的 launchpad 保持未知，DEX/discovery source 单独记录。

Live BSC verification requires user-provided BSC RPC/WS, verified factory addresses, PostgreSQL and Redis. 未提供配置时诊断明确显示 NOT_CONFIGURED/UNKNOWN。

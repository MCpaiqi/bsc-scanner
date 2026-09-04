# Phase 6 仓库审计

**审计日期：** 2026-09-04（UTC）  **基线：** `4f5e5d8`

## 审计结论

Phase 5 已提供配置驱动的 Flap 适配器边界，可以在明确选择器下标准化 provider 记录、解码日志并注入 bonding-curve 读取器。Phase 6 已补齐只读 BSC RPC、验证元数据、有限范围扫描、原始证据留存、幂等事件仓储和单 Token 历史编排，但真实生产摄取仍由验证门禁保护。

## 数据入口

```text
ChainProvider
  -> FlapTokenDiscovery（TokenCreated 日志）
  -> FlapLifecycleDecoder（创建与生命周期）
  -> FlapTradeDecoder（BUY / SELL）
  -> Token、TradeEvent、TokenLifecycleEvent
  -> RawChainRecord + 幂等仓储
  -> Wallet + WalletTokenPerformance
```

发现和回填必须指定 `from_block` 与 `to_block`，BSC RPC 会对日志范围分块、有限重试并缓存区块时间戳。实时订阅仍是明确占位，不属于本阶段。

## BSC 提供器能力

支持 `eth_blockNumber`、`eth_chainId`、`eth_getBlockByNumber`、`eth_getTransactionByHash`、`eth_getTransactionReceipt`、`eth_getLogs` 和只读 `eth_call`。所有原始响应保留在 `raw` 字段；金额先保留原始整数，再以 `Decimal` 标准化，禁止使用浮点数。

## 数据库与身份

现有 `tokens`、`wallets`、`trade_events`、`token_lifecycle_events`、`wallet_token_performance`、`raw_chain_records` 和 `ingestion_runs` 足以承载当前事实层。EVM 交易/生命周期事件身份为 `(chain, tx_hash, event_index)`，其中 `event_index` 等于回执 `log_index`。`20260904_0006` 拒绝为旧生命周期行生成伪造索引；在线升级前必须人工回填，离线 SQL 不执行该检查。

## 已知缺口

- Factory、Router、bonding-curve factory/implementation 和每 Token 曲线地址尚未全部验证。
- 完整 JSON ABI、生命周期事件字段位置、毕业/迁移/流动性语义仍不完整。
- 真实 SELL fixture、真实 PostgreSQL backfill 和完整 Token -> BUY -> SELL -> Wallet 闭环尚未提交。
- `unknown_events` 在未完成全量事件扫描前保持 `UNKNOWN`，不得错误显示为零。

## 禁止事项

不得猜测合约地址、ABI、topic、字段索引、曲线公式、毕业阈值、税费或供应量；不得把 Transfer/LP/空投/内部路由当作 BUY/SELL；不得启用 WebSocket、实盘交易或私钥签名；不得把未来 ATH、最终 ROI 或最终持有人状态泄漏到历史信号。

## 最小后续步骤

先提交真实创建、BUY、SELL 原始 fixture，核对 emitter 和 ABI 布局，再执行单 Token PostgreSQL 回填并保存 `FlapDataQualityReport`。只有 creation、BUY、SELL、时间戳、事件身份和原始证据全部可追溯，才可标记 `INGESTION_SUCCESS`。

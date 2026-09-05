# Phase 6 仓库审计

**审计日期：** 2026-09-04（UTC）  **基线：** `4f5e5d8`

## 审计结论

Phase 5 提供了配置驱动的 Flap 适配器边界；Phase 6 补齐只读 BSC RPC、验证元数据、有限范围扫描、原始证据留存、幂等事件仓储和单 Token 历史编排，但真实生产摄取仍受验证门禁保护。

## 已实现

- BSC RPC 支持 chain id、区块、交易、回执、日志、`eth_call`、`eth_getCode`，日志分块、有限重试和区块时间戳缓存。
- Flap discovery/trade/lifecycle decoder、原始回执/日志/区块保存、`(chain, tx_hash, event_index)` 幂等身份。
- Token、TradeEvent、TokenLifecycleEvent、Wallet、WalletTokenPerformance 仓储及 Position/PnL 派生。
- 运行时验证门禁要求 `verification_status=VERIFIED`；`ENABLE_LIVE_TRADING=false`。

## 问题与风险

- Factory、Router、每 Token bonding curve 地址、完整 JSON ABI 和生命周期字段尚未全部验证。
- 真实 TokenCreated、BUY、SELL、同 wallet 闭环及 PostgreSQL 回填尚未提交；现有 fixture 仅为行为测试。
- `unknown_events` 未完成全量扫描时保持 `UNKNOWN`；事件 block 引用缺失与扫描覆盖缺口分开记录。
- 曲线公式、阈值、流动性语义未被独立链上证据证明，不得用于历史价格计算。

## 测试

当前单元/集成测试覆盖解码、topic、Decimal、时间戳缓存、幂等、Position/PnL 和质量报告；测试日志是合成数据，不构成真实链上证据。

## 下一步

通过 BSC RPC/官方部署信息确认 emitter、ABI、真实 creation/BUY/SELL manifest，随后在隔离 PostgreSQL 执行 discover/backfill/persist/query。完成前 Phase 6.1 状态必须为 **BLOCKED**。

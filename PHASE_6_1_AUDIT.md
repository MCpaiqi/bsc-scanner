# Phase 6.1 Audit

审计基线：当前工作树（2026-09-05 UTC）。本审计只记录代码和可复现证据，不把合成测试 fixture 当作真实链上样本。

## A. 已完成

- BSC 只读 JSON-RPC：chain id、区块、交易、回执、日志、`eth_call`、`eth_getCode`。
- `eth_getLogs` 按配置范围分块，按 `(tx_hash, log_index)` 去重，并缓存区块时间戳。
- Flap discovery / trade / lifecycle decoder、原始链上记录和幂等仓储。
- Trade 价格、market cap、liquidity 使用独立字段；Position/PnL 仅从已解码 TradeEvent 派生。
- 运行时验证门禁仍要求 `verification_status=VERIFIED`，仓库配置保持禁用。

## B. 接口或占位实现

- Flap factory、router、每 Token bonding curve 地址未登记。
- 当前 ABI 仅登记事件声明和字段选择器，不是完整合约 JSON ABI。
- 实时订阅和 live trading 仍为明确占位，`ENABLE_LIVE_TRADING=false`。

## C. 依赖真实证据但尚未证明

- 真实 emitter 与 Portal / factory / curve 角色对应关系。
- 可由 RPC 重定位的 TokenCreated、TokenBought、TokenSold 三份最小证据。
- 真实 PostgreSQL 回填结果和同一 wallet 的 BUY→SELL 闭环。
- Bonding curve 历史公式、阈值、流动性语义。

## D. 数据语义风险

- 未提供完整事件扫描时，`unknown_events` 必须保持 `UNKNOWN`。
- 缺失事件的 block 引用与扫描范围 block 覆盖缺口分别记录，不能混为一谈。
- 缺少 token/native amount 的 Position、PnL、ROI 均保持不可用。
- 任何没有 block timestamp 的事件不得以 ingestion time 代替。

## E. 测试覆盖

- 单元/集成测试覆盖解码、topic、Decimal、时间戳缓存、幂等、Position/PnL 和质量报告。
- 现有测试使用合成日志，仅证明行为，不构成真实链上证据。

## F. 下一步必须修复

1. 通过 BSC RPC 或官方部署信息确认真实 emitter、完整 ABI 和每 Token curve。
2. 保存真实 creation / BUY / SELL 的最小 manifest，并用 `smms verify flap --tx` 重放。
3. 在隔离 PostgreSQL 执行 discover/backfill/persist/query，核对唯一约束和质量报告。

结论：**BLOCKED**。阻塞原因是当前工作树没有足够的真实 SELL 及 emitter 证据，不能宣称 Phase 6.1 完成。

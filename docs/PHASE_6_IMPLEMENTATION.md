# Phase 6 实施状态

**状态：** 部分完成；由于链上证据缺失，生产历史摄取仍受门禁保护。

## 本阶段已实现

- 新增 `src/smms/launchpads/flap/verification/`：合约地址、事件 topic、Keccak 计算、曲线事实、证据等级和机器可读验证报告。
- 在 YAML 中记录已验证的 Portal 地址和事件选择器，同时保持 `launchpads.flap.enabled: false`。
- 扩展只读 BSC 提供器：端点故障转移、有限重试/退避、`eth_call`、`eth_chainId` 校验、日志分块去重和区块时间戳缓存。
- 修正 Flap trade decoder 的 price/market-cap 语义；小数位未知时拒绝生成未经验证的固定点值。
- 为生命周期事件增加 `event_index`，并新增 Alembic revision `20260904_0006`，使身份与 EVM 回执日志索引一致。
- 新增 TradeEvent、TokenLifecycleEvent、WalletTokenPerformance 的同步/异步幂等仓储。
- 新增 `FlapHistoricalIngestionService`、原始回执/日志/区块保存、加权平均成本计算和数据质量报告。
- 新增有界 CLI：`smms discover flap` 与 `smms backfill flap --token ...`；省略区块范围时会拒绝执行。
- 增加 `verification.status` 配置门禁；运行时必须为 `VERIFIED`，仓库配置继续保持 `VERIFY_ON_CHAIN`。
- 缺失交易金额会让钱包/Token 财务派生值保持不可用；质量报告包含创建者，未测量的 unknown-event 数量显示为 `UNKNOWN`；只有 BUY 的样本不能通过完整交易门禁。
- `20260904_0006` 拒绝为旧生命周期行伪造日志索引；旧数据必须先人工完成身份回填。

## 运行路径

```text
BSCProvider
  -> FlapHistoricalIngestionService
  -> FlapAdapter / 已验证选择器
  -> Token、TradeEvent、TokenLifecycleEvent
  -> RawChainRecord + 幂等仓储
  -> Wallet、WalletTokenPerformance
```

服务不传入数据库会返回标准化结果和质量报告；传入异步会话时，会在一次回填操作中提交原始证据和规范行。

## 尚未通过的验证门禁

真实 Token 的 `INGESTION_SUCCESS` 尚不能宣称。仍需证明创建/交易发出者地址，提交真实 TokenCreated、TokenBought、TokenSold 原始 fixture，验证 Portal ABI 和每 Token 曲线状态，并对 PostgreSQL 执行回填和质量报告。

当前测试使用确定性的合成日志，只用于行为覆盖，不能视为真实链上证据。没有把合成 SELL fixture 标记为真实数据。

## 安全范围

`ENABLE_LIVE_TRADING=false` 保持默认值；本阶段没有签名器、私钥处理、swap 提交或实时 WebSocket 摄取，也没有实现 Smart Money 评分。

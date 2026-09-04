# Phase 3 仓库审计

**审计日期：** 2026-09-04  **基线：** `4f5e5d8`

## 结论

项目是 Python 3.12+、SQLAlchemy/Alembic、FastAPI 的多链研究平台。Phase 0-3 已完成基础配置、数据库、只读数据提供器、标准化、历史赢家发现和早期买家识别；Phase 4-6 已加入链无关接口、BSC/Flap 适配器和受验证门禁保护的历史摄取路径。

## 当前分层

```text
FastAPI / CLI
  -> 配置、数据库、Redis 健康检查
数据摄取（DataProvider、MockProvider、SolanaRPCProvider、BSCProvider）
  -> 标准化层
  -> 原始链上记录与幂等仓储
  -> Token、TradeEvent、生命周期、钱包表现
  -> 历史赢家、早期买家、评分、信号、回测、模拟执行
```

## 已完成能力

- `Token`、`Wallet`、钱包交易、Token-Wallet 事件、评分、Signal、PaperTrade、RawChainRecord 和 IngestionRun 数据模型。
- Solana JSON-RPC 与确定性 MockProvider，原始响应按 `(provider, record_type, external_id)` 幂等保存。
- 按显式 `as_of` 和回看窗口发现历史赢家，避免把未来信息带入样本。
- 在 Token 创建后的时间窗口内识别 BUY 早期买家，并保留 Bot 证据。
- 链无关 `chains/base`、`launchpads/base`、只读 BSC RPC、Flap Token discovery/trade/lifecycle/curve 适配器。
- 验证优先的 Flap 地址、事件 topic、ABI 元数据、证据等级、有限范围扫描、原始证据保存、幂等写入和数据质量报告。

## 仍需关注的问题

- Flap 创建/交易发出者地址、完整 ABI、每 Token 曲线状态、真实 BUY/SELL fixture 仍需独立链上证据。
- `Token` 的 ATH 等结果字段必须与 signal-time 特征分离，所有回测遵循 point-in-time 约束。
- `token_lifecycle_events` 使用 `(chain, tx_hash, event_index)` 身份；旧数据库行需要人工回填，迁移不会伪造索引。
- WebSocket、实盘交易、私钥签名、Smart Money 自动评分和完整风险分析不在当前阶段。

## 不应破坏的边界

- 不删除或重写现有 Solana provider、MockProvider、Phase 3 历史赢家/早期买家契约和既有迁移。
- 不把 Flap 地址、ABI、曲线公式或毕业阈值硬编码进核心模块。
- 未验证事实一律标记 `VERIFY_ON_CHAIN` 或 `UNKNOWN`，不能用零或猜测值替代。

## 建议验证顺序

1. 固化真实 TokenCreated、TokenBought、TokenSold 回执/日志 fixture。
2. 核对每个事件的 emitter、topic、indexed/data 布局和时间戳。
3. 通过 PostgreSQL 执行单 Token 回填，检查原始记录、规范事件、钱包和质量报告。
4. 在真实闭环通过后，再扩展多 Token、研究评分和回测。

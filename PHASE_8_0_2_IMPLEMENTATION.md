# Phase 8.0.2：BSC 单链可靠性与数据摄取正确性加固

生产 runtime 仅支持 BNB Smart Chain（BSC），Chain ID 为 56。Solana 仅作为历史数据 provenance 保留，不再是当前生产运行链。

## 已完成的可靠性边界

- 统一 BSC_CHAIN_ID、BSC-only chain validation 和 EVM 地址 normalization。
- HTTP RPC endpoint 激活前验证 eth_chainId、eth_blockNumber 和单块 eth_getLogs；错链 endpoint 被隔离。
- WebSocket 激活前验证 eth_chainId 和 eth_subscribe，状态信息不暴露完整 URL。
- WS 失败后使用 HTTP polling；failback 按 candidate subscribe、bounded buffer、barrier、HTTP catch-up、dedup、buffer drain、WS active 顺序执行，并使用连续健康探测阈值。
- durable StreamCheckpoint 使用稳定的 bsc/bsc_realtime 身份；只有 raw event 与下游 projection 成功后才推进并提交 checkpoint，恢复从 durable checkpoint 减去有限 reorg lookback 开始。
- raw event identity 包含 chain_id、transaction_hash、log_index 和 block_hash，允许 reorg replacement canonical log 重新进入；HTTP/WS 重复 replay 由仓储幂等吸收。
- 图谱、source events、edges 和 pair checks 全部有界；钱包 ranking 使用数据库排序和 limit。
- GoPlus 缺少凭据、超时、限流、认证失败或无数据时保持 UNKNOWN/DEGRADED，不会推断为 SAFE。Flap 继续 FLAP_NOT_VERIFIED / VERIFY_ON_CHAIN。
- 前端保持中文用户界面，仅显示 BSC，不提供 Solana chain selector。

本阶段不启用真实交易，不开始 Phase 8.1，不删除历史 Solana 数据，也没有破坏性数据库迁移；新增 migration 仅调整 raw event replacement identity 约束。

## 验证结果

- `pytest -q`：241 passed，11 warnings（依赖弃用提示）。
- `ruff check src tests scripts`：通过。
- `web`：`npm test -- --run`（5 passed），`npm run build` 通过。
- 16 项 Phase 8.0.2 专项测试覆盖错链 HTTP/WS、边界 replay、zero-gap failback、handoff failure、durable checkpoint、reorg replacement、graph/ranking bound、status 脱敏、WS 配置别名、告警规则与风险列表的 BSC-only 边界，以及 nullable block hash 的唯一性。
- 未配置真实 BSC provider，因此未运行 live BSC verification。

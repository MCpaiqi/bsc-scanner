# 架构说明

Phase 0 建立配置、日志、依赖边界和健康检查；Phase 1 增加关系型数据库结构与仓储；Phase 2 增加数据提供器边界和标准化层：提供器返回权威原始数据，标准化层生成规范记录但不改写来源。Phase 4 加入链无关的链/发射平台接口、只读 BSC 提供器和“先验证后启用”的 Flap 适配器。Phase 6 增加有限范围的 BSC RPC、事件验证元数据、幂等事件仓储，以及单 Token 历史摄取流程。

所有外部服务必须通过提供器适配器访问。缺少凭据时，健康检查明确返回 `not_configured`；系统不会制造数据。

Solana 适配器只调用有文档依据的 JSON-RPC 方法。当前主要目标是 BNB Smart Chain 上的 Flap，Solana 继续作为并行提供器。Flap 特有的事件选择器和曲线读取器位于 `launchpads/flap`。已经确认的选择器可以写入配置，但在事件发出者地址和真实 fixture 完整前，发射平台仍保持禁用。DEX swap 解码属于后续数据增强，不能把任意 Token 转账推断成交易。

历史数据路径严格分层：

```text
BSC JSON-RPC
  -> Flap 适配器 / 解码器
  -> 标准化 Token、TradeEvent、TokenLifecycleEvent
  -> 原始链上证据 + 幂等仓储
  -> PostgreSQL + 时间点一致的钱包/Token 计算
```

实时 WebSocket 摄取和实盘执行不在当前范围内。

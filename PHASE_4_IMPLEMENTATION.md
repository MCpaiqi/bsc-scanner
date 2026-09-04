# Phase 4 实施说明

## 已交付

- 建立链无关的 BSC 与发射平台接口。
- 在 `src/smms/launchpads/flap/` 增加配置驱动的 Flap 适配器。
- 支持从配置的工厂创建日志发现 Flap Token。
- 支持从交易回执和日志解码规范化 Flap `TradeEvent`。
- 支持交易开始、接近毕业、毕业和 DEX 上市等生命周期事件的配置化解码。
- 增加注入式 bonding curve 读取器；缺少验证事实时明确抛出 `DataUnavailable`。
- 在 `configs/default.yaml` 和 `configs/sources.yaml` 增加 Flap 配置占位。

## 验证边界

仓库不包含已验证的 Flap 合约地址、完整 ABI、事件字段位置、曲线参数、手续费、毕业阈值或 Token 小数位。所有缺口必须在链上或官方来源验证后再填写。当前 YAML 保持禁用，不会伪造这些事实。

交易方向必须由 BUY/SELL topic、side 字段或显式默认值确定。只有通用事件 topic 时，系统会拒绝解码。

## 尚未实现

- Flap 实时 WebSocket 摄取。
- 完整验证 ABI 与 bonding curve 数学。
- 流动性、税费、持有人和合约风险分析。
- Flap 发现/扫描及数据库持久化编排的完整 CLI。
- 实盘交易；`ENABLE_LIVE_TRADING` 默认保持关闭。

## 测试

Flap 单元测试覆盖创建日志发现、BUY 解码、毕业生命周期映射和验证曲线读取器边界；完整测试套件保持 Solana 行为不变。

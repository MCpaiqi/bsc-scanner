# 系统架构

SMMS 是一个多链、多发射平台的聪明钱 Meme 情报引擎。

```text
链数据提供器（ChainProvider）
    -> 发射平台适配器
        -> Token / TradeEvent / TokenLifecycleEvent
            -> 研究、评分、信号、回测、模拟执行
```

BSC 提供器负责有限范围的 JSON-RPC 传输、重试与故障转移、区块时间戳，以及区块、交易、回执和日志的标准化。Flap 适配器负责经过验证的选择器和解码逻辑；这些细节不会泄漏到核心标准化或聪明钱模块中。历史摄取流程会先保存原始证据，再写入规范事件记录。

当前主要目标：BNB Smart Chain 上的 Flap。

现有支持：Phase 2 的 Solana 提供器继续作为并行数据源保留。

安全边界：当前阶段不实现实盘交易。未知的 Flap 链上事实必须作为 `VERIFY_ON_CHAIN` 配置缺口保留，不能用猜测值代替。在创建/交易事件发出者角色和真实 BUY/SELL fixture 完成验证前，仓库内 Flap 配置保持禁用。

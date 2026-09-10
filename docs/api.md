# API 说明

当前 API 提供 BSC 最新代币资料发现和依赖健康检查。钱包、信号、模拟交易和回测接口将在对应阶段实现后加入。

## BSC 市场发现

`GET /api/bsc/tokens?limit=18` 返回 DexScreener 最新发布资料中的 BSC 代币，不代表全市场活跃榜。每个代币只保留流动性最高的主交易池，包含价格、市值、流动性、成交量、买卖笔数、涨跌和外部详情链接。`limit` 允许 `1..30`。开发时可显式添加 `demo=true` 获取本地演示数据；响应中的 `source=demo` 和 `demo=true` 用于防止客户端将其误认为实时行情。

这是公开市场发现流，不等同于已验证的 Flap 新币流。当前 Flap 事件地址仍处于链上验证阶段，聪明钱评分和合约审计也尚未加入；API 不生成替代数据。

## 数据提供器接口

`DataProvider` 提供 `get_token`、`get_transactions` 和 `get_swaps`。`MockProvider` 具有确定性，用于测试和离线开发。`SolanaRPCProvider` 需要配置 `SOLANA_RPC_URL`；当数据源未配置、不可达或缺少权威数据时，会抛出 `DataUnavailable`。

原始数据按 `(provider, record_type, external_id)` 幂等键保存到 `raw_chain_records`。只有必需字段完整时才生成标准化记录；缺少时间戳、签名或地址的数据会被 `NormalizationError` 拒绝。

健康检查响应中的字段名和状态值属于稳定的机器接口，继续使用英文标识，避免破坏已有客户端。FastAPI 页面标题和所有面向人的说明使用中文。

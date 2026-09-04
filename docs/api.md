# API 说明

当前 API 提供 `GET /health` 健康检查。Token、钱包、信号、模拟交易和回测接口将在对应阶段实现后加入。

## 数据提供器接口

`DataProvider` 提供 `get_token`、`get_transactions` 和 `get_swaps`。`MockProvider` 具有确定性，用于测试和离线开发。`SolanaRPCProvider` 需要配置 `SOLANA_RPC_URL`；当数据源未配置、不可达或缺少权威数据时，会抛出 `DataUnavailable`。

原始数据按 `(provider, record_type, external_id)` 幂等键保存到 `raw_chain_records`。只有必需字段完整时才生成标准化记录；缺少时间戳、签名或地址的数据会被 `NormalizationError` 拒绝。

健康检查响应中的字段名和状态值属于稳定的机器接口，继续使用英文标识，避免破坏已有客户端。FastAPI 页面标题和所有面向人的说明使用中文。

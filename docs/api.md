# API 说明

当前 API 提供 BSC 最新代币资料发现、钱包画像、聪明钱雷达和依赖健康检查。交易执行接口不在本阶段范围内。

## Wallet Intelligence

`GET /api/wallet/{address}/profile?chain=bsc&as_of=<ISO-8601>` 从规范化历史
交易重放单个钱包的完整 `WalletProfile`。画像包含已/未平仓、胜负、ROI 与中位数、
multiple 命中、回撤、集中度、流动性依赖和行为指标；`as_of` 用于历史回放并避免
未来数据泄漏。历史不足 4 个 token 样本时置信度为 `LOW`。

`GET /api/radar/smart-money/wallets` 返回钱包画像列表，支持 `sort=score|roi|win_rate|sample_size|profit_factor`、
`min_sample`、`min_score`、`limit`、`chain` 和 `as_of` 参数。

Phase 8 Wallet Intelligence endpoints:

- `GET /api/wallet/{address}/clusters?chain=bsc&as_of=<ISO-8601>`：关系与 Cluster Intelligence。
- `GET /api/wallet/{address}/graph?chain=bsc&as_of=<ISO-8601>`：Wallet、Token、Trade、Cluster 节点图谱。
- `GET /api/token/{address}/smart-money-flow?chain=bsc&window=1m|5m|15m|1h|24h`：资金流窗口。
- `POST /api/wallet/replay`：输入成功 Meme 列表，回放提前进入与重复钱包。

Phase 8 未指定 `chain` 时默认使用 BSC；显式传入 `chain=bsc` 正常，其他链值会返回 400。当前生产 runtime 仅支持 BNB Smart Chain（Chain ID 56）。

## BSC 市场发现

`GET /api/bsc/tokens?limit=18` 返回 DexScreener 最新发布资料中的 BSC 代币，不代表全市场活跃榜。每个代币只保留流动性最高的主交易池，包含价格、市值、流动性、成交量、买卖笔数、涨跌和外部详情链接。`limit` 允许 `1..30`。开发时可显式添加 `demo=true` 获取本地演示数据；响应中的 `source=demo` 和 `demo=true` 用于防止客户端将其误认为实时行情。

这是公开市场发现流，不等同于已验证的 Flap 新币流。当前 Flap 事件地址仍处于链上验证阶段，聪明钱评分和合约审计也尚未加入；API 不生成替代数据。

## 数据提供器接口

`DataProvider` 提供 `get_token`、`get_transactions` 和 `get_swaps`。`MockProvider` 具有确定性，用于测试和离线开发。生产链数据由 BSC provider 提供；当数据源未配置、不可达或缺少权威数据时，会抛出 `DataUnavailable`。历史 Solana provider 代码仅供旧数据工具兼容，不参与 active runtime。

原始数据按 `(provider, record_type, external_id)` 幂等键保存到 `raw_chain_records`。只有必需字段完整时才生成标准化记录；缺少时间戳、签名或地址的数据会被 `NormalizationError` 拒绝。

健康检查响应中的字段名和状态值属于稳定的机器接口，继续使用英文标识，避免破坏已有客户端。FastAPI 页面标题和所有面向人的说明使用中文。
`GET /api/bsc/stream/status` returns persisted worker/checkpoint state. `python -m smms diagnostics live-bsc` performs a bounded read-only diagnostic. Radar market fields include `volume_1m`, `volume_5m`, source, freshness and `reason_codes`.

The live BSC stream is fail-closed when no verified factory is configured. Runtime
status is read from the shared `runtime_worker_status` projection; it does not
instantiate a second ingestion worker. `TokenMarketSnapshot` distinguishes
`ON_CHAIN_TRADE_AGGREGATION` from `DEXSCREENER`, and USD volume includes only trades
with a known USD valuation. Quote/native amounts remain separate and may be known
while USD fields are `UNKNOWN`.

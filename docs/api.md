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


## 研究备注与提醒范围

工作台备注按 `(chain, token_address)` 共享保存；当前未按用户分区。`GET /api/radar/annotations/{chain}/{token}` 返回 `revision`，不存在或已删除时返回404。写入 `PUT` 必须带 `expected_revision`：0表示“当前不存在时创建”，正数表示“仅更新此版本”。未带条件返回428；版本已变化、被删除或被其他写入创建时返回409，客户端应保留草稿并读取最新版本，不能自动覆盖。正文仍为 `favorite`、`note`、`tags`。删除使用 `DELETE ...?expected_revision=<当前版本>`，清除备注内容并保留递增的身份/版本标记；重新创建不会复用旧的正数版本。

`POST /api/radar/alert-rules` 接受明确的 `scope`：

- `TOKEN`：必须提供有效 BSC `token_address`，只在此链、此合约上评估。
- `GLOBAL`：`token_address` 必须为空，匹配规则所在链上的代币。兼容旧客户端的默认范围为GLOBAL；详情界面默认选择TOKEN，并展示完整合约地址。

响应包含 `scope`、`token_address` 和 `request_id`。`PATCH /api/radar/alert-rules/{id}` 仅修改显式提供的字段，省略范围和地址会保留现有目标；明确选择GLOBAL时可以清除旧目标。`request_id` 不允许修改或清除。

创建时可提供UUID格式的 `request_id`。相同编号和相同正文的重试返回同一规则，正文不同返回409；规则删除后隐藏并停用，保留历史及请求标识，旧创建重试不会复活该规则。没有编号的旧客户端创建请求仍不具有幂等保证。浏览器为最近256个规则定义缓存请求编号，在同一标签页的关闭/重开详情、参数切换和刷新后重用；禁用存储时仅保留当前页面内存缓存。缓存不包含备注、凭据或访问令牌。

部署代码前先运行 `python -m alembic upgrade head`，0045迁移会给旧备注设置版本1，保留旧规则原有的GLOBAL范围，不从规则名称推断合约。包含新范围、请求标识、删除标记或已使用的备注版本时，降级会拒绝丢失这些元数据。权限和部署暴露范围仍需按可靠性计划R13验收。

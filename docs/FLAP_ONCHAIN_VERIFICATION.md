# Flap 链上验证登记册

**验证日期：** 2026-09-04（UTC）  **网络：** BNB Smart Chain 主网  **Chain ID：** `56`

本文是证据登记册，不是已启用的生产配置。只有记录了来源和验证方法的事实，才能提升到适配器配置；未列为已验证的内容一律保持 `BLOCKED: VERIFY_ON_CHAIN`。

## 证据等级

| 等级 | 含义 |
| --- | --- |
| `VERIFIED` | 直接来自链上日志、回执、区块、合约调用或权威官方文档 |
| `DERIVED` | 仅由已验证值确定性计算，例如 topic 哈希、UTC 时间戳或 Decimal 标准化 |
| `INFERRED` | 根据多个已验证现象推断的生命周期或角色解释，不能伪装成直接事件 |
| `UNKNOWN` | 没有可靠来源 |

未知值必须保持未知，不能替换成零或看似合理的默认值。

## 官方来源

- [Flap 已部署合约地址](https://docs.flap.sh/flap/developers/deployed-contract-addresses)：官方 BNB 部署地址列表。
- [TokenCreated 事件](https://docs.flap.sh/flap/developers/wallet-and-terminal-and-bot-developers/index-token-created-events.md)：创建事件声明和索引方式。
- [交易 Token](https://docs.flap.sh/flap/developers/wallet-and-terminal-and-bot-developers/trade-tokens.md)：BUY/SELL 事件声明和字段。
- [开发者视角的 bonding curve](https://docs.flap.sh/flap/developers/wallet-and-terminal-and-bot-developers/bonding-curve-in-developers-perspective.md)：供应量、精度和曲线术语。

## 合约角色

| 角色 | 地址 / 状态 |
| --- | --- |
| Portal | `0xe2cE6ab80874Fa9Fa2aAE65D277Dd6B8e65C9De0`，官方地址页已验证 |
| Factory / 创建发出者 | `BLOCKED: VERIFY_ON_CHAIN` |
| Router | `BLOCKED: VERIFY_ON_CHAIN` |
| Bonding-curve factory / implementation | `BLOCKED: VERIFY_ON_CHAIN` |
| 每 Token 曲线 | `BLOCKED: VERIFY_ON_CHAIN`，必须按 Token 读取 |

Portal 地址不能被静默复制为 `factory_address`、`router_address` 或 `bonding_curve_address`。

## 已登记事件

### 创建

```text
TokenCreated(uint256,address,uint256,address,string,string,string)
topic0 = 0x504e7f360b2e5fe33cbaaae4c593bc55305328341bf79009e43e0e3b7f699603
creator data index = 1
token data index   = 3
```

声明和字段布局已有官方/真实日志证据，但启用前仍必须确认实际 emitter 地址。

### 交易

```text
TokenBought(uint256,address,address,uint256,uint256,uint256,uint256)
topic0 = 0xa800a2038683844fac66747f771bfdfae862eb28b16bcfa387afa9fbacce8ff7

TokenSold(uint256,address,address,uint256,uint256,uint256,uint256)
topic0 = 0x03a4693e592f5e75dc7c136acb39b146d2b4966c0e509c34f362dee02b3b861a
```

观察到的七个 data word 顺序为：`0 timestamp`、`1 token`、`2 trader`、`3 token amount`、`4 native amount`、`5 fee`、`6 post-trade price`。`TokenSold` 的真实历史 SELL fixture 仍为 `ON_CHAIN_FIXTURE_REQUIRED`。Transfer、LP、手续费分配、创建者分配和内部路由只有在事件语义明确证明用户买卖时才能转成 TradeEvent。

### 候选生命周期 topic

`LaunchedToDEX`、`FlapTokenProgressChanged`、`FlapTokenCirculatingSupplyChanged`、`TokenCurveSetV2`、`TokenDexSupplyThreshSet`、`TokenQuoteSet` 的 topic 只是候选计算结果，均为 `BLOCKED: VERIFY_ON_CHAIN`。在真实 ABI 和日志确认前，`GRADUATED`、`DEX` 只能是 `INFERRED` 或 `UNKNOWN`。

## 历史候选记录（不可作为真实证据）

以下地址来自早期脱敏/占位材料，因其呈现规律化尾缀，**不得视为真实链上 Token 或 Creator**。真实证据必须从 BSC RPC 重新发现并写入 `data/evidence/flap/manifest.json`。

| 字段 | 已验证值 |
| --- | --- |
| Token | `UNKNOWN`（旧材料中的规律化占位地址已弃用） |
| Creator | `UNKNOWN`（旧材料中的规律化占位地址已弃用） |
| Name / Symbol | `UNKNOWN` |
| Creation block | `UNKNOWN` |
| Block timestamp | `UNKNOWN` |
| Creation transaction | `UNKNOWN` |
| Creation log index | `UNKNOWN` |

该历史候选记录不再作为证据；Factory 角色、曲线地址和交易闭环仍待真实 RPC 复核。

## Bonding curve 状态

官方文档支持：最大供应量 `1_000_000_000`、Token 小数位 `18`、constant-product 术语、参数名 `r`/`h`/`k`，以及通过 Portal 的 `getTokenV8`（旧文档曾写 `getTokenV5`）读取状态。每 Token 参数必须在请求区块读取并缓存；曲线公式、阈值、流动性和历史状态仍为 `BLOCKED: VERIFY_ON_CHAIN`。因此当前 reader 继续采用注入式边界。

## RPC 风险

`https://bsc-dataseed.binance.org/` 的宽范围 `eth_getLogs` 会触发 `limit exceeded`；`https://bsc-rpc.publicnode.com` 的有限查询可用，但曾出现瞬时 TLS `UNEXPECTED_EOF`。生产读取必须使用有限区块范围、分块、有限重试/退避和端点故障转移。

## 启用门禁

启用 Flap 前必须附带：真实回执证明 emitter、完整 ABI 和字段位置、真实创建/BUY/SELL raw fixture、由区块解析的时间戳、可按区块读取的每 Token 曲线状态，以及 `(chain, tx_hash, event_index)` 幂等身份。全部满足前，配置保持禁用，`ENABLE_LIVE_TRADING=false` 保持不变。

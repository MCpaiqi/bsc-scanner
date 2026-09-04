# Phase 3 Repository Audit

审计日期：2026-09-04  
审计范围：当前工作树 `master`（commit `6b719c9`），未修改既有业务代码。  
基线验证：`pytest -q` -> **20 passed, 1 warning**（FastAPI/HTTPX deprecation warning）。

## A. 当前架构

项目是一个 Python 3.12+、SQLAlchemy/Alembic、FastAPI 的单体研究骨架，包根为 `src/smms`。当前分层如下：

```text
API (FastAPI /health)
  └── config + database/redis health

ingestion
  ├── DataProvider protocol
  ├── MockProvider
  └── solana/SolanaRPCProvider (JSON-RPC)
        ↓
normalization (raw provider payload -> canonical dictionaries)
        ↓
db repositories + raw_chain_records (provider-scoped idempotency)

smart_money
  ├── historical winner discovery
  └── early buyer identification

db/models
  ├── Token / Wallet
  ├── WalletTransaction / TokenWalletEvent
  ├── WalletScore / TokenScore
  ├── Signal / PaperTrade
  └── RawChainRecord / IngestionRun

execution
  ├── PaperExecution
  └── LiveExecution (guarded, raises by design)
```

数据库当前以单个初始 Alembic revision 建表，SQLite 可用于单元测试，默认运行时数据库为 PostgreSQL，Redis 仅用于健康检查。

## B. 已完成模块

- Phase 0 基础：Pydantic Settings、日志脱敏、FastAPI `/health`、Docker Compose、纸面交易默认值和 live safety guards。
- Phase 1 数据库：`Token`、`Wallet`、钱包交易、token-wallet 事件、版本化 wallet/token score、signal、paper trade、原始链记录、ingestion run；同步/异步 repository 均存在。
- Phase 2 数据接入：`DataProvider` 协议、确定性的 `MockProvider`、`SolanaRPCProvider`；原始响应写入 `raw_chain_records`，以 `(provider, record_type, external_id)` 幂等；normalization 会拒绝缺少签名、钱包或时间戳的数据。
- Phase 3 历史研究核心：`HistoricalTokenSnapshot`、`HistoricalWinnerConfig`、`discover_historical_winners()`；按显式 `as_of` 和 lookback 过滤，避免将未来 token 纳入观察集。
- Phase 3 早期买家核心：`EarlyBuyerConfig`、`EarlyBuyer`、`identify_early_buyers()`；按 token 创建后窗口筛选 BUY、按钱包去重并聚合买入 USD，保留 bot evidence。
- 回归测试覆盖上述边界、repository 幂等/cascade、provider 调用方式、设置安全闸门和 health endpoint。

## C. 未完成模块

- 没有统一的 `TradeEvent`、`TokenLifecycleEvent` 或可回溯的 token 生命周期流水。
- `Token` 仍是市场快照模型，不包含 launchpad、creator、decimals/supply、first trade、graduation、ATH 时间/价格等完整生命周期字段。
- `EarlyBuyer` 只有首笔时间、总买入 USD、入场市值和一笔交易签名，缺少买卖计数、持仓、PnL、ATH capture、graduation survival、buy rank 等研究字段。
- 没有 `WalletTokenPerformance`、`WalletPerformance`、可配置且可解释的 `SmartMoneyScore` 计算器。
- 没有 point-in-time backtest/replay、consensus 计算或 signal 生成流程；现有 `Signal` 只是持久化表。
- 没有 `chains/base`、`chains/bsc`、`launchpads/base` 或 `launchpads/flap`；不存在 BSC RPC/WebSocket、合约日志、Flap token discovery/trade decoder/bonding-curve adapter。
- 没有 token risk filter/risk flags、data-quality report 或风险评分实现。
- CLI 仅接受 `health/init-db/ingest/scan/backtest/paper`，除 health 外都输出 later-phase 占位信息；没有 `discover flap`、`scan flap`、`analyze-wallet`、`analyze-token`、`data-quality`。
- API 只有健康检查；没有研究查询端点。
- `scripts/backfill_*`, `seed_data.py`, `run_backtest.py`, `run_paper.py` 和若干包仅为占位入口。

## D. Solana-specific coupling

1. **Settings/config**：`Settings` 只定义 `SOLANA_RPC_URL`/`SOLANA_WS_URL`；`.env.example`、`configs/sources.yaml` 只描述 Solana，默认配置没有链/launchpad 层级。
2. **Provider package**：唯一真实 provider 是 `ingestion/solana/rpc.py`，并直接调用 Solana JSON-RPC 方法 `getAccountInfo`、`getSignaturesForAddress`、`getTransaction`。
3. **Normalization defaults**：`normalize_token()` 对缺失 chain 默认写入 `solana`；同步和异步 token/wallet repository 的默认 chain 也是 `solana`。
4. **Domain enum/defaults**：`Chain` enum 只有 `SOLANA`，`Token.chain`/`Wallet.chain` 默认值为 `solana`。
5. **Schema/docs/product naming**：README、`pyproject.toml` description、API title、`docs/data-model.md`、`docs/api.md` 等将产品描述为 Solana Smart Money Scanner，文档中的 token 表也写成 Solana token profile。
6. **Tests**：现有 repository fixtures 默认使用 Solana，provider 回归测试只验证 Solana RPC。

这些耦合目前没有破坏已有 Solana 行为，但会阻碍 BSC/Flap 作为并列适配器接入。当前没有发现硬编码的 Solana program ID 或伪造的 Flap 地址/参数，这是应保留的谨慎边界。

## E. 可以复用的代码

- `db.models.Base`、UTC 时间工具、Numeric 金额类型和 Alembic 迁移约定。
- `(chain, address)` 唯一约束和同步/异步 upsert repository 模式，可直接扩展到多链。
- `RawChainRecord` 的 provider-scoped identity、原始 payload 保存和 ingestion observability。
- `DataProvider` 协议、`DataUnavailable`、`IngestionService` 的 raw-first 流程，以及 `MockProvider` 测试 seam。
- `normalization/records.py` 的 Decimal、UTC 和 authoritative-field 校验策略；应抽取/扩展为统一事件转换层。
- `HistoricalTokenSnapshot`/`HistoricalWinnerConfig`/`discover_historical_winners()` 的 as-of 过滤思想。
- `EarlyBuyerConfig`、`identify_early_buyers()` 的窗口、去重、排序和 bot evidence 逻辑；需向 richer model 兼容扩展。
- `Signal`、`PaperTrade` 表和 `LiveExecution` 的安全闸门；Phase 4 只能生成研究 signal，不能打开 live execution。
- 现有 20 个单元测试作为回归基线，尤其是 normalization、raw idempotency、cascade 和 look-ahead 相关的 as-of 测试。

## F. 必须重构的代码

后续实施应采用增量迁移，保留现有 Solana provider 和 Phase 3 函数：

1. **Domain models/schema**：扩展 `Token`、增加统一 `TradeEvent`/`TokenLifecycleEvent`、wallet-token 和 wallet aggregate performance、smart-money score 所需模型及索引；通过新的 Alembic revision，不修改旧 revision 的历史语义。
2. **Chain seam**：新增 `chains/base` 协议和 `chains/bsc` 最小 RPC/log/block provider；把 chain-specific raw payload 留在 adapter 内，再映射到统一模型。
3. **Launchpad seam**：新增 `launchpads/base` lifecycle/discovery/trade/bonding-curve 接口，Flap 实现放在 `launchpads/flap`，不得把 Flap 分支散落到 core/normalization。
4. **Provider/config**：将 RPC、WebSocket、chain id、factory/portal/router/curve 地址迁移到可配置结构；无法验证的地址必须保留 `TODO: VERIFY_ON_CHAIN`，不能填猜测值。
5. **Smart-money analytics**：保留现有 early buyer 输入，增加行为聚合、point-in-time wallet performance、配置化 score 权重和 consensus signal。
6. **Risk/data quality**：新增只读风险检查和数据质量报告，所有未来数据使用必须显式区分 research outcome 与 signal-time features。
7. **CLI/API/docs**：在现有入口上增加最小可运行命令和查询，而不是删除旧命令；同步 README、架构和数据模型文档。

## G. 当前数据模型问题

- `Token` 缺少 `launchpad`，使同一地址语义无法表达多 launchpad；也缺 creator、decimals、total supply、created/first trade/graduation/ATH 时间、ATH price、graduation liquidity、status、outcome class 等字段。
- `Token` 当前把部分结果字段（ATH、volume）和当前快照混放，缺少明确的 observation timestamp/version，容易在回测时产生 look-ahead bias。
- `WalletTransaction` 是 wallet 视角的交易记录，`TokenWalletEvent` 是另一套 token 事件记录；没有统一 tx hash、block number、event index、source、native/token amount 和 side 的规范事件模型。
- 交易 signature 只在 wallet 范围内唯一；跨链/跨 launchpad 统一事件需要 `(chain, tx_hash, event_index[, source])` 级幂等策略。
- `EarlyBuyer` 为不可持久化 dataclass，且只有首笔/汇总买入信息，无法回答完整生命周期行为问题。
- `Wallet` 上的 `win_rate`、`realized_pnl` 等是可变汇总字段，没有 token-level position ledger、快照时间或 score version，无法审计跨 token 聚合过程。
- `WalletScore`/`TokenScore` 字段虽有版本和时间，但没有实现计算器，也没有明确 point-in-time feature contract。
- `Signal` 没有产生逻辑、输入快照或 look-ahead 标记；`PaperTrade` 也没有与回测 observation 绑定。
- 缺少 creator/top-holder/holder concentration、tax、mint/blacklist/pause 等风险事实的来源和时间维度。

## H. Phase 4 推荐方案

按主提示词的最小垂直切片执行：

1. **先做模型兼容层**：给 `Token` 和交易域增加可扩展 `chain`/`launchpad` 字段、统一枚举/字符串策略、生命周期事件和索引；保留旧列/旧 dataclass 的兼容读写，先让旧 20 个测试继续通过。
2. **建立接口边界**：`chains/base` 只描述链数据能力；`launchpads/base` 描述 discovery、creation、trade decoding、lifecycle、bonding curve；core 只消费统一 domain models。
3. **最小 BSC provider**：实现 RPC、receipt/log/block timestamp 获取和明确的 `DataUnavailable`；不依赖网页 DOM。
4. **Flap adapter 第一阶段**：仅实现 token discovery/creation detection、trade decoding、bonding-curve state/lifecycle 输出。官方合约事件、地址和曲线参数在确认前保持配置 TODO。
5. **研究链路**：把 Flap/Solana 输出统一为 `Token`、`TradeEvent`、`TokenLifecycleEvent`；从事件构建 richer `EarlyBuyer`、`WalletTokenPerformance`、`WalletPerformance` 和可配置 Smart Money Score。
6. **研究安全层**：所有 signal features 采用 `as_of`/event timestamp 的 point-in-time 查询；ATH、最终 outcome、未来 holder 状态只能用于标签/评估，不能用于当时的入场判断。
7. **风险、共识和 CLI**：先实现可解释 risk flags、基础多钱包 consensus、只生成 `Signal`；加入 `discover flap`、`scan flap`、`analyze-wallet`、`analyze-token`、`data-quality` 的离线/配置缺失时明确失败行为。
8. **最后同步文档和集成测试**：增加 BSC RPC -> receipt/log -> Flap decoder -> TradeEvent -> database 的 fixture 测试，同时保留所有 Solana 回归测试；不要在本阶段实现真实资金交易。

建议的新增目录：

```text
src/smms/chains/base/
src/smms/chains/bsc/
src/smms/chains/solana/          # 逐步从 ingestion/solana 迁移或提供兼容导出
src/smms/launchpads/base/
src/smms/launchpads/flap/
src/smms/tokens/{lifecycle,metrics,risk,classification}/
src/smms/wallets/{history,performance,clustering,bot_detection,scoring}/
src/smms/smart_money/{historical_winners,early_buyers,wallet_score,consensus,ranking}/
```

## I. 风险

- **链上事实未验证**：仓库没有 Flap 官方合约 ABI、地址、事件签名或 bonding-curve 参数；任何实现若自行填值都会制造假数据。应先标记 `VERIFY_ON_CHAIN` 并使未配置路径显式不可用。
- **Look-ahead bias**：现有 `Token.ath_*` 等字段是结果字段，若直接参与历史 signal 计算会泄漏未来信息；需要 observation/as-of 约束和回测审计测试。
- **事件语义不一致**：Solana provider 当前只返回保守的 `UNKNOWN` transaction，Flap BUY/SELL 解码不能假设所有 receipt/log 都是用户交易，必须校验事件来源和 event index。
- **金额/精度风险**：BSC 原生 BNB 与 token raw units、税费、曲线报价需要 Decimal/raw integer 处理，不能引入 float。
- **迁移兼容风险**：生产库已有初始 revision；新增字段/表必须以新 revision 和可回滚策略交付，避免重写 `20260904_0001`。
- **默认安全风险**：当前 `Settings` 允许显式 LIVE 配置，但 Phase 4 需求是 `ENABLE_LIVE_TRADING=false`；后续命令和 adapter 应继续拒绝真实执行，不要把研究 signal 自动连接到 execution。
- **数据可用性风险**：没有可验证的 Flap 历史数据 fixture，无法声称 discovery/scan 已在真实链上工作；集成测试应使用明确标注的 fixture，并将 live RPC 作为可选验证。
- **产品语义漂移**：README、API title、包描述和 docs 仍称 Solana scanner；若不更新会继续误导使用者把系统当成 Solana-only。

## J. 修改文件清单

### 本审计步骤新增

- `PHASE_3_AUDIT.md`（本文件）。

### 后续 Phase 4 预计需要修改

- `src/smms/db/models/entities.py`
- `src/smms/db/models/__init__.py`
- `src/smms/db/migrations/versions/<new_phase4_revision>.py`
- `src/smms/db/repositories/`（新增统一事件/performance/query repositories，保留现有实现）
- `src/smms/normalization/records.py`、`src/smms/ingestion/types.py`、`src/smms/ingestion/service.py`
- `src/smms/ingestion/solana/`（兼容导出或迁移到 `chains/solana`，不删除 Solana）
- `src/smms/config/settings.py`、`.env.example`、`configs/default.yaml`、`configs/sources.yaml`、`configs/scoring.yaml`、`configs/risk.yaml`
- `src/smms/smart_money/early_buyers.py`、`src/smms/smart_money/__init__.py` 及新增 wallet/performance/scoring/consensus 模块
- `src/smms/main.py`、`src/smms/api/app.py`
- `README.md`、`docs/architecture.md`、`docs/data-model.md`、`docs/api.md`、`docs/backtesting.md`、`docs/strategy.md`
- 新增 `src/smms/chains/`、`src/smms/launchpads/`、风险/数据质量模块及对应 tests。

### 本阶段明确不应修改/删除

- 不修改或删除现有 `HistoricalTokenSnapshot`、`HistoricalWinnerConfig`、`discover_historical_winners()` 的行为契约。
- 不删除 `ingestion/solana/rpc.py`、Solana tests、`MockProvider`、raw-record 幂等逻辑。
- 不删除 `execution/live.py` 的安全闸门，也不启用真实交易。
- 不重写初始 migration；不删除已有 Phase 1/2/3 tests。
- 不在 core 中嵌入 Flap 特殊地址、事件签名或 bonding-curve 猜测值。

## 审计结论

当前仓库已经提供了可复用的 raw-first ingestion、数据库一致性和历史赢家/早期买家研究起点，但还不是可运行的 Smart Money Meme Intelligence Engine，也尚未开始 Flap/BSC 支持。最小安全路径是增量扩展统一 domain model 和 adapter seams，再以经过链上验证的 Flap 事实驱动实现；在此之前不应宣称 Phase 4 的实时扫描、评分、回测或交易能力已经完成。

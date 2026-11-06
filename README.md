# BSC Meme Radar · 聪明钱 Meme 情报引擎

SMMS 是一个链上研究平台。当前产品界面聚焦 BNB Smart Chain，由已确认的链上事件发现 Radar Candidate；DexScreener 与 GoPlus 只提供候选 enrichment 和 secondary evidence。

生产运行链为 BNB Smart Chain（BSC，Chain ID 56）。当前 runtime 仅支持 BSC；历史 Solana 数据仍保留 provenance，但不再作为生产数据源摄取。

系统默认运行于 `PAPER`（模拟交易）模式，目前没有实现真实资金执行。未来即使加入实盘适配器，也必须同时开启 `ENABLE_LIVE_TRADING` 和 `LIVE_CONFIRMATION` 两个安全开关。

## 快速开始

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
Copy-Item .env.example .env
docker compose up --build
```

启动 Web 情报台与 API：

```powershell
uvicorn smms.api.app:app --reload
```

打开 `http://127.0.0.1:8080/` 即可使用 React Trader Workstation。`/` 默认是工作台，旧版静态页面仅保留在 `/legacy`。标准 Compose 拓扑会同时启动 API、Web、BSC ingestion、Radar audit、Forward outcome、Alpha coverage 和研究快照重建 workers，以及 PostgreSQL、Redis。

当前服务启动前需完成 `20261106_0039` 迁移。Alpha coverage worker 使用确认后的 HTTP 区块扫描证明数据覆盖；缺证据的目标结果保持 UNKNOWN。当前评分版本为 `experimental-v3`，详见 [覆盖与恢复口径](docs/ALPHA_COVERAGE.md)。失效的 Alpha 输入会保留历史并自动创建替代修订，可查询 [来源和重建记录](docs/ALPHA_RECONSTRUCTION.md)。市场、风险、钱包/Token 聪明钱、早期买家和持仓快照会隐藏失效版本并在后台生成新修订；历史买家提取已升级为 `dataset-v4`，详见 [派生研究来源与恢复](docs/DERIVED_RESEARCH.md)。

无外网时可打开 `http://127.0.0.1:8000/?demo=1` 预览完整卡片交互。该模式只在显式指定时启用，页面会持续标明数据为演示数据，不会在真实行情失败时自动顶替。

当前公开市场接口提供价格、市值、流动性、成交量、买卖笔数和涨跌数据。聪明钱评分、钱包画像和 Token 资金流从版本化 point-in-time snapshot 读取；未执行聚合或指定时点没有 snapshot 时会明确返回 `UNKNOWN`，不会用模拟值填充。Risk Engine 可消费 Evidence Resolver 的结论，并保留来源、置信度、新鲜度和冲突信息。界面为只读模式，不连接钱包也不执行交易。

持有人分析和合约风险检查通过 Evidence Resolver 接入链上与外部证据；链上证据优先，冲突保持可见。Phase 7.5.1 只加固既有 Smart Money 链路，外部钱包标签不会覆盖 Smart Money Score。

API 提供：

- `GET /api/bsc/tokens`：BSC 活跃代币市场快照，可用 `limit=1..30` 限制数量。
- `GET /api/wallet/{address}/profile`：读取指定版本和 `as_of` 时点的钱包画像 snapshot。
- `GET /api/radar/smart-money/wallets`：从 snapshot 排序筛选聪明钱钱包。
- `GET /api/radar/tokens/{token}/smart-money`：读取 Token Smart Money snapshot。
- `GET /api/wallet/{address}/clusters`：读取钱包关系、同步交易和 Cluster Intelligence。
- `GET /api/wallet/{address}/graph`：读取 Wallet/Token/Trade 关系图谱。
- `GET /api/token/{address}/smart-money-flow`：读取 1m/5m/15m/1h/24h 聪明钱净流入流出。
- `POST /api/wallet/replay`：输入历史成功 Meme，回放提前进入、共同出现和重复钱包。
- `GET /health`：数据库、Redis 和外部数据源健康检查。

未配置外部数据源时，健康检查会明确返回 `not_configured`，不会伪造数据。

代币名称和符号由 `bsc-ingestion` 的独立后台任务自动补全，即使关闭 DexScreener，
仍会通过 BSC RPC 读取 ERC-20 元数据。失败项会轮转重试；DexScreener 也可为已由链上
发现的交易对补上缺失名称。运行 API 和 Web 时请同时运行 ingestion worker，
无需单独启动 `scripts/alpha_presentation_enrichment.py`。雷达列表、详情和搜索均支持名称；
数据源尚未提供名称时显示地址和待补全状态。

生成或重算 snapshot：

```powershell
smms intelligence refresh --chain bsc --as-of 2026-09-11T10:00:00+00:00
```

## Flap 历史命令

```powershell
smms discover flap --from-block 100 --to-block 200
smms backfill flap --token 0x... --from-block 100 --to-block 200
```

历史扫描必须显式指定有限区块范围，并且只有启用且通过验证的 Flap 配置才能运行。仓库内默认配置仍为 `enabled: false` 和 `VERIFY_ON_CHAIN`。

## 安全边界

- 运行模式默认为 `PAPER`。
- 实盘执行仍是受保护的占位实现；任一安全开关关闭时都会拒绝运行。
- 密钥应放在 `.env` 或外部密钥管理器中；Git 已忽略 `.env`。
- 所有持久化时间戳统一使用 UTC。
- 未验证的合约地址、事件布局和曲线参数必须保持 `VERIFY_ON_CHAIN`，不得猜测。

当前架构和证据状态见 [架构说明](docs/architecture.md)、[Phase 6 仓库审计](docs/PHASE_6_AUDIT.md) 和 [Flap 链上验证](docs/FLAP_ONCHAIN_VERIFICATION.md)。

Production Radar 诊断见 [Phase 7.9.3 实现说明](PHASE_7_9_3_IMPLEMENTATION.md)。`/health`、`/diagnostics` 和 `/api/radar/diagnostics` 提供不含密钥的 Source Health、链滞后、队列、Provider 与 SSE 指标；数据块显式返回 `observed_at`、`updated_at` 和 `freshness`，stale 数据不会标记为实时。

## Phase 7.7.3 — Fusion Readiness Hardening

Historical universe 依据当时可观察的生命周期构建。Launchpad Token 无需等待 DEX Pair；
DEX-only Token 从有时间戳的 Pair 创建事实开始进入 universe。未来 Pair 或 migration 不回填过去状态。
数据集将 `feature_price_history`（≤ as_of）与 `evaluation_price_path`（> as_of）分开。
旧数据集应重建以获得修正后的 universe；旧 prices 格式仍在回测边界兼容。

开发与 CI 使用同一个入口（Python 3.12+，建议先激活虚拟环境）：

```powershell
./scripts/test.ps1
```

```bash
bash scripts/test.sh
# 跨平台等价入口，可追加 pytest 参数，例如 -q
python scripts/test.py
```

入口执行 `pip install -e ".[dev]"`、pytest、ruff check；无需 PYTHONPATH。
固定 hash seed / UTC、显式加载 pytest-asyncio，禁用外部插件自动加载，使用独立临时目录，
并设置 `ENABLE_LIVE_TRADING=false`。依赖版本仍采用 pyproject 中的范围，不是锁文件复现。

发布前先生成项目目录外的 release artifact。默认输出为项目旁的
`../artifacts/smms-release/`（及同名 zip），因此不会把输出再次复制进 source tree：

```bash
python scripts/build_release.py
```

构建器会排除 `.git/`、`.venv/`、`venv/`、`node_modules/`、`__pycache__/`、
`.pytest_cache/`、`.ruff_cache/`、`.mypy_cache/`、`build/`、`dist/`、`coverage/`、
`release/`、`tmp/`、`cache/`、`temporary/`、测试目录、`.env`、本地数据库和
临时 Phase/pytest/release-test 目录、日志、数据库转储和 TypeScript 构建缓存。
`data/` 只保留 `.gitkeep` 目录占位文件，不分发本地运行数据。
发布工具脚本仍包含在 artifact 中。重复运行命令会先清理同一 artifact，再重新构建。
如需指定目录，使用 `--output`；即使指定项目内目录，构建器也会跳过输出目录，
并拒绝把 source root 本身或其祖先目录作为输出。
源目录按脚本位置确定；相对 `--output` 路径相对于源目录解析，与调用时的工作目录无关。

发布前审计实际待分发目录：

```bash
python scripts/release_audit.py <release-directory>
```

仅输出 path / reason / severity；发现风险返回非零。对工作区运行
`python scripts/release_audit.py .` 会报告 Git、缓存、临时目录等开发产物。
应准备独立发布目录后重审：

Phase 8.0.1 将 Wallet Intelligence 的主产品语义收敛到 BSC：默认链为 bsc，地址按 EVM lower-case identity 查询，所有 profile、graph、cluster、flow 和 replay 读取遵守 as_of 且有服务端上限。图谱响应会返回 truncated 与截断计数；风险响应保留 overall_risk 并提供 risk_state，缺少证据时为 UNKNOWN，不会显示为安全。

BSC realtime 支持 BSC_WS_URLS/BSC_WS_URL 与 BSC_RPC_URLS/BSC_RPC_URL。WebSocket 连续失败后通过 checkpoint 切换到 HTTP eth_getLogs polling；恢复必须通过多次健康探测和 catch-up 才 failback。DexScreener 是带 timeout、rate limit、TTL/stale cache 的 secondary evidence，GoPlus 使用 app-key/app-secret 获取 Bearer token。Flap 在真实证据完成前继续 VERIFY_ON_CHAIN 且禁用。完整范围见 PHASE_8_0_1_IMPLEMENTATION.md。

```bash
python scripts/release_audit.py ../artifacts/smms-release
```

确认审计结果不包含 `.env`、`.git`、`node_modules`、cache/temporary files，且不存在
`release/release` 或更深层递归路径。详见 [实现说明](PHASE_7_7_3_IMPLEMENTATION.md)。



## release脚本

~~~powershell
python scripts/build_release.py
~~~

## 启动命令

~~~
docker compose up --build
uvicorn smms.api.app:app --host 127.0.0.1 --port 8000
npm run dev --prefix web
~~~

~~~
docker compose down
docker compose up -d --build --force-recreate
~~~

Phase 8.0.4 closes the live BSC path: verified factory PairCreated events feed a
bounded ActivePairRegistry and dynamic Swap/Sync subscriptions; shared transaction
context resolves router callers; canonical TradeEvent rows preserve explicit token,
quote, native and USD units; and background DexScreener enrichment updates only
already discovered pairs before Radar/Redis/SSE projection. Use
`python -m smms diagnostics live-bsc` for a read-only health check. Live smoke tests
remain `NOT RUN` until verified BSC RPC/WS, factory, PostgreSQL and optional Redis
settings are supplied.

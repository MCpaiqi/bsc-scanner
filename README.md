# BSC Meme Radar · 聪明钱 Meme 情报引擎

SMMS 是一个链上研究平台。当前产品界面聚焦 BNB Smart Chain，用于发现 DexScreener 最新发布资料的 Meme 代币，并逐步接入聪明钱钱包识别、可解释信号、回测和模拟交易。

当前主要目标发射平台是 BNB Smart Chain 上的 Flap。现有 Solana 数据提供器继续作为并行数据源保留。所有发射平台特有逻辑均隔离在链无关的适配器接口之后。

系统默认运行于 `PAPER`（模拟交易）模式，目前没有实现真实资金执行。未来即使加入实盘适配器，也必须同时开启 `ENABLE_LIVE_TRADING` 和 `LIVE_CONFIRMATION` 两个安全开关。

## 快速开始

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
Copy-Item .env.example .env
docker compose up -d postgres redis
python scripts/init_db.py
pytest
```

启动 Web 情报台与 API：

```powershell
uvicorn smms.api.app:app --reload
```

打开 `http://127.0.0.1:8000/` 即可使用 BSC 情报台。页面每 30 秒刷新 DexScreener 最新资料中的 BSC 代币，并从其交易对中选择流动性最高的主池；后端以 20 秒缓存合并多个页面的重复请求。界面支持名称/符号/地址搜索，以及 1 小时成交额、创建时间、流动性、24 小时成交量和涨幅排序。这是最新资料样本，不代表全市场活跃榜。

无外网时可打开 `http://127.0.0.1:8000/?demo=1` 预览完整卡片交互。该模式只在显式指定时启用，页面会持续标明数据为演示数据，不会在真实行情失败时自动顶替。

当前公开市场接口提供价格、市值、流动性、成交量、买卖笔数和涨跌数据。聪明钱评分与合约审计尚未接入，页面会明确标记为“待接入”，不会用模拟值填充。界面为只读模式，不连接钱包也不执行交易。

本提交是第一阶段市场工作台：交付截图风格的 BSC-only 界面与可运行行情链路。截图中的聪明钱钱包计数、持有人分析和合约风险检查需要后续接入已验证的实时计算管线，不属于当前已完成能力。

API 提供：

- `GET /api/bsc/tokens`：BSC 活跃代币市场快照，可用 `limit=1..30` 限制数量。
- `GET /health`：数据库、Redis 和外部数据源健康检查。

未配置外部数据源时，健康检查会明确返回 `not_configured`，不会伪造数据。

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

# 聪明钱 Meme 情报引擎

SMMS 是一个多链研究平台，用于识别聪明钱钱包的早期活动、生成可解释信号，并通过回测和模拟交易验证研究假设。

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

启动 API：

```powershell
uvicorn smms.api.app:app --reload
```

API 提供 `GET /health` 健康检查。未配置外部数据源时，系统会明确返回 `not_configured`，不会伪造数据。

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

from fastapi import FastAPI

from smms.config.settings import get_settings
from smms.db.database import check_database
from smms.db.redis import check_redis

app = FastAPI(title="聪明钱 Meme 情报引擎", version="0.1.0", description="多链 Meme 研究与数据摄取 API")


@app.get("/health")
async def health() -> dict[str, object]:
    settings = get_settings()
    database = await check_database(settings)
    redis = await check_redis(settings)
    return {
        "status": "ok" if database == "ok" and redis == "ok" else "degraded",
        "database": database,
        "redis": redis,
        "solana_rpc": "ok" if settings.solana_rpc_url else "not_configured",
        "bsc_rpc": "ok" if settings.bsc_rpc_url else "not_configured",
        "market_provider": "ok" if settings.market_api_key else "not_configured",
        "trading_mode": settings.trading_mode,
        "live_trading_enabled": settings.enable_live_trading,
    }

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from smms.api.demo import demo_bsc_tokens
from smms.config.settings import get_settings
from smms.db.database import check_database
from smms.db.redis import check_redis
from smms.ingestion import DataUnavailable
from smms.ingestion.market import DexScreenerMarketProvider, MarketFeedService

STATIC_DIR = Path(__file__).with_name("static")

app = FastAPI(title="BSC Meme Radar", version="0.1.0", description="BSC Meme 只读情报与数据 API")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
market_feed = MarketFeedService(provider=DexScreenerMarketProvider())


@app.get("/", include_in_schema=False)
async def dashboard() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/bsc/tokens")
async def bsc_tokens(
    limit: int = Query(18, ge=1, le=30), demo: bool = Query(False)
) -> dict[str, object]:
    if demo:
        return demo_bsc_tokens(limit)
    try:
        return await market_feed.latest_tokens(limit=limit)
    except DataUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


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
        "market_feed": "ready",
        "market_provider": "ok" if settings.market_api_key else "not_configured",
        "trading_mode": settings.trading_mode,
        "live_trading_enabled": settings.enable_live_trading,
    }

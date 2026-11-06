from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import Numeric, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import aliased

from smms.api.analyst import router as analyst_router
from smms.api.demo import demo_bsc_tokens
from smms.api.radar import alpha_router
from smms.api.radar import meta_router as radar_meta_router
from smms.api.radar import router as radar_router
from smms.api.research import router as research_router
from smms.backtest.signal_engine import run_backtest
from smms.chains.constants import (
    BSC_CHAIN_ID,
    DEFAULT_CHAIN,
    normalize_address,
    normalize_bsc_address,
    validate_bsc_chain,
)
from smms.config.settings import get_settings
from smms.db.database import check_database, session_scope
from smms.db.models import (
    BacktestDataset,
    BacktestDatasetRow,
    MarketEvent,
    RuntimeWorkerStatus,
    StreamCheckpoint,
    Token,
    TokenMarketSnapshot,
    TokenRiskSnapshot,
    TokenSmartMoneySnapshot,
    TradeEvent,
    WalletSmartMoneySnapshot,
    WorkerHeartbeat,
)
from smms.db.redis import check_redis
from smms.ingestion import DataUnavailable
from smms.ingestion.market import DexScreenerMarketProvider, MarketFeedService
from smms.observability import freshness_status, metrics, observability, source_health_registry
from smms.radar.audit import AuditQueue
from smms.radar.events import event_bus, subscribe_redis
from smms.risk.engine import RISK_VERSION
from smms.smart_money import (
    EarlyBuyerConfig,
    build_wallet_knowledge_graph,
    calculate_smart_money_alpha,
    detect_wallet_clusters,
    replay_historical_smart_money,
    smart_money_flow,
)
from smms.smart_money.aggregation import SCORE_VERSION
from smms.smart_money.replay import StaleReplayInputs, load_replay_inputs

STATIC_DIR = Path(__file__).with_name("static")
_WEB_DIST_CANDIDATES = (
    Path(__file__).resolve().parents[3] / "web" / "dist",
    Path("/app/web/dist"),
)
WEB_DIST = next((path for path in _WEB_DIST_CANDIDATES if path.is_dir()), _WEB_DIST_CANDIDATES[0])

app = FastAPI(title="BSC Meme Radar", version="0.1.0", description="BSC Meme 只读情报与数据 API")
app.include_router(radar_router)
app.include_router(radar_meta_router)
app.include_router(alpha_router)
app.include_router(analyst_router)
app.include_router(research_router)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
if WEB_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="web-assets")
_runtime_settings = get_settings()
market_feed = MarketFeedService(
    provider=DexScreenerMarketProvider(
        base_url=_runtime_settings.dexscreener_base_url,
        timeout=_runtime_settings.dexscreener_timeout_seconds,
        rate_limit_per_second=_runtime_settings.dexscreener_rate_limit_per_second,
        cache_ttl_seconds=_runtime_settings.dexscreener_cache_ttl_seconds,
        api_key=_runtime_settings.dexscreener_api_key.get_secret_value()
        if _runtime_settings.dexscreener_api_key
        else None,
    ),
    ttl=timedelta(seconds=_runtime_settings.dexscreener_cache_ttl_seconds),
)
_redis_subscriber_task = None


@app.on_event("startup")
async def start_runtime_subscribers() -> None:
    """Start cross-instance SSE fanout without making Redis mandatory."""
    import asyncio

    global _redis_subscriber_task

    async def bridge() -> None:
        try:
            await subscribe_redis()
        except Exception as exc:  # noqa: BLE001 - optional infrastructure is degraded
            source_health_registry.record_failure("redis", exc)

    _redis_subscriber_task = asyncio.create_task(bridge())


@app.on_event("shutdown")
async def stop_runtime_subscribers() -> None:
    if _redis_subscriber_task is not None:
        _redis_subscriber_task.cancel()


async def _dataset_or_404(
    dataset_id: int, session
) -> tuple[BacktestDataset, list[dict[str, object]]]:
    dataset = await session.scalar(select(BacktestDataset).where(
        BacktestDataset.id == dataset_id, BacktestDataset.chain == "bsc",
    ).execution_options(include_invalid_snapshots=True, populate_existing=True))
    if dataset is None:
        raise HTTPException(status_code=404, detail="backtest dataset not found")
    await _assert_dataset_current(session, dataset_id)
    records = (
        await session.scalars(
            select(BacktestDatasetRow)
            .where(BacktestDatasetRow.dataset_id == dataset.id)
            .order_by(BacktestDatasetRow.signal_timestamp)
            .execution_options(populate_existing=True)
        )
    ).all()
    await _assert_dataset_current(session, dataset_id)
    from copy import deepcopy

    return dataset, [deepcopy(record.payload_json) for record in records]


async def _assert_dataset_current(session, dataset_id):
    current = await session.scalar(select(BacktestDataset.id).where(BacktestDataset.id == dataset_id))
    if current is None:
        raise HTTPException(status_code=409, detail={
            "status": "REBUILDING", "reason": "DATASET_INVALIDATED", "dataset_id": dataset_id,
            "lineage_url": f"/api/research/snapshots/backtest_datasets/{dataset_id}",
        })


def _trade_payload(row: TradeEvent) -> dict[str, object]:
    side = row.side.value if hasattr(row.side, "value") else row.side
    return {
        "wallet_address": row.trader_address,
        "signature": row.tx_hash,
        "event_index": row.event_index,
        "timestamp": row.timestamp,
        "side": side,
        "token_address": row.token_address,
        "token_amount": row.token_amount,
        "native_amount": row.native_amount,
        "usd_value": row.value_usd,
        "price": row.price,
        "price_usd": row.price_usd,
        "market_cap": row.market_cap,
        "liquidity": row.liquidity,
        "block_number": row.block_number,
        "metadata_json": row.metadata_json,
    }


def _cutoff(value: datetime | None) -> datetime:
    cutoff = value or datetime.now(UTC)
    return cutoff.replace(tzinfo=UTC) if cutoff.tzinfo is None else cutoff.astimezone(UTC)


def _validated_chain(value: str | None) -> str:
    try:
        return validate_bsc_chain(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _validated_address(value: str) -> str:
    try:
        return normalize_bsc_address(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _freshness_fields(
    observed_at: datetime | None, updated_at: datetime | None
) -> dict[str, object]:
    observed = _cutoff(observed_at) if observed_at is not None else None
    updated = _cutoff(updated_at) if updated_at is not None else None
    current = datetime.now(UTC)
    reference = observed or updated
    return {
        "observed_at": observed,
        "updated_at": updated,
        "freshness": freshness_status(reference, now=current) if reference else "UNKNOWN",
        "data_freshness_seconds": (
            max(0, int((current - reference).total_seconds())) if reference else None
        ),
    }


def _flap_readiness() -> dict[str, object]:
    """Report Flap configuration state without probing or inventing chain facts."""

    from smms.launchpads.flap import FlapConfig

    repository_root = Path(__file__).resolve().parents[3]
    paths = (
        repository_root / "configs" / "sources.yaml",
        repository_root / "configs" / "default.yaml",
        Path("configs/sources.yaml"),
        Path("configs/default.yaml"),
    )
    path = next((candidate for candidate in paths if candidate.is_file()), None)
    if path is None:
        return {
            "flap_configured": False,
            "flap_verified": False,
            "flap_ready": False,
            "flap_status": "NOT_CONFIGURED",
        }
    try:
        config = FlapConfig.from_yaml(path)
    except (OSError, TypeError, ValueError, RuntimeError):
        return {
            "flap_configured": False,
            "flap_verified": False,
            "flap_ready": False,
            "flap_status": "FLAP_NOT_VERIFIED",
        }
    verified = config.verification_status == "VERIFIED"
    ready = bool(config.enabled and verified)
    if ready:
        try:
            config.validate_runtime()
        except ValueError:
            ready = False
    return {
        "flap_configured": bool(config.enabled),
        "flap_verified": verified,
        "flap_ready": ready,
        "flap_status": "READY"
        if ready
        else ("FLAP_NOT_VERIFIED" if not verified else "NOT_CONFIGURED"),
    }


def _snapshot_provenance(row):
    return {"research_revision": row.research_revision, "source_epoch": row.source_epoch,
            "previous_snapshot_id": row.previous_snapshot_id,
            "input_digest": (row.input_manifest or {}).get("digest"),
            "lineage_url": f"/api/research/snapshots/{row.__tablename__}/{row.id}"}


def _wallet_snapshot_payload(
    row: WalletSmartMoneySnapshot, *, network_influence: Decimal | float = 0
) -> dict[str, object]:
    payload = dict(row.profile_json)
    alpha = calculate_smart_money_alpha(payload, network_influence=network_influence)
    payload.update(
        {
            **_snapshot_provenance(row),
            "wallet": row.wallet_address,
            "score": row.score,
            "smart_money_score": row.score,
            "confidence": row.confidence,
            "confidence_score": row.confidence_score,
            "score_version": row.score_version,
            "profile_version": row.profile_version,
            "as_of": row.as_of,
            "sample_size": row.sample_size,
            "data_completeness": row.data_completeness,
            "historical_coverage": row.historical_coverage,
            "trade_quality": row.trade_quality,
            "components": row.components_json,
            "contributions": row.contributions_json,
            "normalized_features": row.components_json,
            "alpha_score": alpha.score,
            "alpha_components": {
                "historical_skill": alpha.historical_skill,
                "current_behavior": alpha.current_behavior,
                "network_influence": alpha.network_influence,
                "risk_adjustment": alpha.risk_adjustment,
            },
            "alpha_contributions": alpha.contributions,
            "alpha_explanation": alpha.explanation,
            **_freshness_fields(row.as_of, row.updated_at),
        }
    )
    return payload


def _token_snapshot_payload(row: TokenSmartMoneySnapshot) -> dict[str, object]:
    return {
        **_snapshot_provenance(row),
        "token_address": row.token_address,
        "smart_wallet_count": row.smart_wallet_count,
        "smart_money_buy_usd": row.smart_money_buy_usd,
        "smart_money_sell_usd": row.smart_money_sell_usd,
        "smart_money_net_flow": row.smart_money_net_flow,
        "top_smart_wallets": row.top_smart_wallets_json,
        "early_smart_buy_count": row.early_smart_buy_count,
        "smart_money_score": row.smart_money_score,
        "confidence": row.confidence,
        "confidence_score": row.confidence_score,
        "data_status": row.data_status,
        "as_of": row.as_of,
        "score_version": row.score_version,
        **_freshness_fields(row.as_of, row.updated_at),
    }


def _risk_snapshot_payload(row: TokenRiskSnapshot) -> dict[str, object]:
    dimensions = row.dimensions_json or {}
    return {
        **_snapshot_provenance(row),
        "token": row.token_address,
        "contract_risk": row.contract_risk,
        "liquidity_risk": row.liquidity_risk,
        "holder_risk": row.holder_risk,
        "behavior_risk": row.behavior_risk,
        "bot_probability": row.bot_probability,
        "wash_probability": row.wash_probability,
        "overall_risk": row.overall_risk,
        "risk_state": row.overall_risk,
        "confidence": row.confidence,
        "evidence": row.evidence_json,
        "dimensions": dimensions,
        "as_of": row.as_of,
        "risk_version": row.risk_version,
        **_freshness_fields(row.as_of, row.updated_at),
    }


def _token_metadata(rows: list[Token]) -> dict[str, dict[str, object]]:
    metadata: dict[str, dict[str, object]] = {}
    for row in rows:
        metadata[row.address] = {
            "created_at": row.creation_timestamp or row.first_seen_at,
            "graduation_at": row.graduation_at,
            "outcome_class": row.outcome_class or row.status,
            **(row.metadata_json if isinstance(row.metadata_json, dict) else {}),
        }
    return metadata


@app.get("/", include_in_schema=False)
async def dashboard() -> FileResponse:
    if (WEB_DIST / "index.html").is_file():
        return FileResponse(WEB_DIST / "index.html")
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/legacy", include_in_schema=False)
async def legacy_dashboard() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/bsc/tokens")
async def bsc_tokens(
    limit: int = Query(18, ge=1, le=30), demo: bool = Query(False)
) -> dict[str, object]:
    if demo:
        return demo_bsc_tokens(limit)
    try:
        payload = await market_feed.latest_tokens(limit=limit)
        if isinstance(payload, dict):
            observed = payload.get("observed_at") or payload.get("updated_at")
            payload.setdefault("observed_at", observed)
            payload.setdefault("freshness", "UNKNOWN" if observed is None else "FRESH")
            payload.setdefault(
                "data_status", "UNKNOWN" if not payload.get("tokens") else "OBSERVED"
            )
        tokens = payload.get("tokens", [])
        if not tokens:
            return payload
        settings = get_settings()
        addresses = {
            normalize_bsc_address(str(item.get("token_address", "")))
            for item in tokens
            if isinstance(item, dict) and item.get("token_address")
        }
        async with session_scope(settings) as session:
            def materialize(sync):
                from smms.market.snapshots import materialize_dexscreener_snapshot
                for item in tokens:
                    if isinstance(item, dict) and item.get("token_address"):
                        materialize_dexscreener_snapshot(sync, item, as_of=datetime.now(UTC))
                sync.flush()
            await session.run_sync(materialize)
            candidate = aliased(TokenSmartMoneySnapshot)
            latest_as_of = (
                select(func.max(candidate.as_of))
                .where(
                    candidate.chain == TokenSmartMoneySnapshot.chain,
                    candidate.token_address == TokenSmartMoneySnapshot.token_address,
                    candidate.score_version == TokenSmartMoneySnapshot.score_version,
                    candidate.as_of <= datetime.now(UTC),
                )
                .correlate(TokenSmartMoneySnapshot)
                .scalar_subquery()
            )
            snapshots = list(
                (
                    await session.scalars(
                        select(TokenSmartMoneySnapshot)
                        .where(
                            TokenSmartMoneySnapshot.chain == "bsc",
                            TokenSmartMoneySnapshot.score_version == SCORE_VERSION,
                            TokenSmartMoneySnapshot.token_address.in_(addresses),
                            TokenSmartMoneySnapshot.as_of == latest_as_of,
                        )
                        .order_by(TokenSmartMoneySnapshot.as_of.desc())
                    )
                ).all()
            )
            risk_rows = list(
                (
                    await session.scalars(
                        select(TokenRiskSnapshot)
                        .where(
                            TokenRiskSnapshot.chain == "bsc",
                            TokenRiskSnapshot.token_address.in_(addresses),
                            TokenRiskSnapshot.risk_version == RISK_VERSION,
                            TokenRiskSnapshot.as_of <= datetime.now(UTC),
                        )
                        .order_by(TokenRiskSnapshot.as_of.desc())
                    )
                ).all()
            )
            market_rows = list(
                (
                    await session.scalars(
                        select(TokenMarketSnapshot)
                        .where(
                            TokenMarketSnapshot.chain == "bsc",
                            TokenMarketSnapshot.token_address.in_(addresses),
                            TokenMarketSnapshot.as_of <= datetime.now(UTC),
                        )
                        .order_by(TokenMarketSnapshot.as_of.desc())
                    )
                ).all()
            )
        latest = {}
        for snapshot in snapshots:
            latest.setdefault(normalize_bsc_address(snapshot.token_address), snapshot)
        latest_risk = {}
        for snapshot in risk_rows:
            latest_risk.setdefault(normalize_bsc_address(snapshot.token_address), snapshot)
        latest_market = {}
        for snapshot in market_rows:
            latest_market.setdefault(normalize_bsc_address(snapshot.token_address), snapshot)
        for item in tokens:
            if not isinstance(item, dict):
                continue
            item.setdefault("observed_at", payload.get("observed_at"))
            item.setdefault("updated_at", payload.get("updated_at"))
            item.setdefault("freshness", payload.get("freshness", "UNKNOWN"))
            item_address = normalize_bsc_address(str(item.get("token_address", "")))
            snapshot = latest.get(item_address)
            risk_snapshot = latest_risk.get(item_address)
            market_snapshot = latest_market.get(item_address)
            if market_snapshot is not None:
                item.update(
                    {
                        "price_usd": market_snapshot.price_usd,
                        "market_cap_usd": market_snapshot.market_cap_usd,
                        "fdv_usd": market_snapshot.fdv_usd,
                        "liquidity_usd": market_snapshot.liquidity_usd,
                        "volume_1m": market_snapshot.volume_1m,
                        "volume_5m": market_snapshot.volume_5m,
                        "buys_1m": market_snapshot.buys_1m,
                        "sells_1m": market_snapshot.sells_1m,
                        "buys_5m": market_snapshot.buys_5m,
                        "sells_5m": market_snapshot.sells_5m,
                        "market_source": market_snapshot.source,
                        "market_as_of": market_snapshot.as_of,
                        "market_freshness_seconds": market_snapshot.freshness_seconds,
                    }
                )
            if risk_snapshot is None:
                item.update(
                    {
                        "contract_risk": "UNKNOWN",
                        "liquidity_risk": "UNKNOWN",
                        "holder_risk": "UNKNOWN",
                        "behavior_risk": "UNKNOWN",
                        "bot_probability": None,
                        "wash_probability": None,
                        "overall_risk": "UNKNOWN",
                        "risk_evidence": [],
                    }
                )
            else:
                item.update(
                    {
                        "contract_risk": risk_snapshot.contract_risk,
                        "liquidity_risk": risk_snapshot.liquidity_risk,
                        "holder_risk": risk_snapshot.holder_risk,
                        "behavior_risk": risk_snapshot.behavior_risk,
                        "bot_probability": risk_snapshot.bot_probability,
                        "wash_probability": risk_snapshot.wash_probability,
                        "overall_risk": risk_snapshot.overall_risk,
                        "risk_evidence": risk_snapshot.evidence_json,
                        "risk_as_of": risk_snapshot.as_of,
                        "risk_version": risk_snapshot.risk_version,
                    }
                )
            if snapshot is None:
                item.update(
                    {
                        "smart_money_score": None,
                        "smart_wallet_count": None,
                        "smart_money_buy_usd": None,
                        "smart_money_sell_usd": None,
                        "smart_money_net_flow": None,
                        "early_smart_buy_count": None,
                        "smart_money_top_wallets": [],
                        "smart_money_data_status": "UNKNOWN",
                    }
                )
                continue
            item.update(
                {
                    "smart_money_score": snapshot.smart_money_score,
                    "smart_wallet_count": snapshot.smart_wallet_count,
                    "smart_money_buy_usd": snapshot.smart_money_buy_usd,
                    "smart_money_sell_usd": snapshot.smart_money_sell_usd,
                    "smart_money_net_flow": snapshot.smart_money_net_flow,
                    "early_smart_buy_count": snapshot.early_smart_buy_count,
                    "smart_money_top_wallets": snapshot.top_smart_wallets_json,
                    "smart_money_data_status": snapshot.data_status,
                    "smart_money_as_of": snapshot.as_of,
                    "smart_money_score_version": snapshot.score_version,
                }
            )
        return payload
    except DataUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/backtest/summary")
async def backtest_summary(
    strategy: str = Query("smart-money"),
    threshold: Decimal = Query(Decimal(50)),  # noqa: B008
) -> dict[str, object]:
    settings = get_settings()
    async with session_scope(settings) as session:
        dataset = await session.scalar(
            select(BacktestDataset).where(BacktestDataset.chain == "bsc")
            .order_by(BacktestDataset.build_timestamp.desc(), BacktestDataset.id.desc()).limit(1)
            .execution_options(include_invalid_snapshots=True)
        )
        if dataset is None:
            return {"status": "INSUFFICIENT_DATA", "datasets": []}
        dataset, rows = await _dataset_or_404(dataset.id, session)
        result = run_backtest(
            rows, strategy=strategy, threshold=threshold
        )
        await _assert_dataset_current(session, dataset.id)
    return result.summary | {
        "status": result.status,
        "strategy": strategy,
        "model_version": result.model_version,
        "dataset_id": dataset.id,
        "dataset_version": dataset.dataset_version,
        **_snapshot_provenance(dataset),
    }


@app.get("/api/backtest/signals")
async def backtest_signals(
    limit: int = Query(100, ge=1, le=1000), strategy: str = Query("smart-money")
) -> dict[str, object]:
    settings = get_settings()
    async with session_scope(settings) as session:
        dataset = await session.scalar(
            select(BacktestDataset).where(BacktestDataset.chain == "bsc")
            .order_by(BacktestDataset.build_timestamp.desc(), BacktestDataset.id.desc()).limit(1)
            .execution_options(include_invalid_snapshots=True)
        )
        if dataset is None:
            return {
                "status": "INSUFFICIENT_DATA",
                "model_version": "signal-v1",
                "signals": [],
                "count": 0,
            }
        dataset, rows = await _dataset_or_404(dataset.id, session)
        result = run_backtest(rows, strategy=strategy)
        await _assert_dataset_current(session, dataset.id)
    return {
        "status": result.status,
        "model_version": result.model_version,
        "dataset_id": dataset.id,
        "dataset_version": dataset.dataset_version,
        **_snapshot_provenance(dataset),
        "signals": [signal.as_dict() for signal in result.signals[-limit:]],
        "count": len(result.signals),
    }


@app.get("/api/backtest/datasets")
async def backtest_datasets(limit: int = Query(50, ge=1, le=200)) -> dict[str, object]:
    settings = get_settings()
    async with session_scope(settings) as session:
        datasets = (
            await session.scalars(
                select(BacktestDataset)
                .order_by(BacktestDataset.build_timestamp.desc())
                .limit(limit)
            )
        ).all()
    return {
        "datasets": [
            {
                "id": row.id,
                "dataset_version": row.dataset_version,
                **_snapshot_provenance(row),
                "chain": row.chain,
                "from": row.period_from,
                "to": row.period_to,
                "build_timestamp": row.build_timestamp,
                "report": row.report_json,
            }
            for row in datasets
        ]
    }


@app.get("/api/backtest/datasets/{dataset_id}/summary")
async def backtest_dataset_summary(
    dataset_id: int,
    strategy: str = Query("smart-money"),
    threshold: Decimal = Query(Decimal(50)),  # noqa: B008
) -> dict[str, object]:
    settings = get_settings()
    async with session_scope(settings) as session:
        dataset, rows = await _dataset_or_404(dataset_id, session)
        result = run_backtest(rows, strategy=strategy, threshold=threshold)
        await _assert_dataset_current(session, dataset.id)
    return result.summary | {
        "status": result.status,
        "dataset_id": dataset.id,
        "dataset_version": dataset.dataset_version,
        **_snapshot_provenance(dataset),
        "coverage": dataset.source_coverage_json,
        "report": dataset.report_json,
    }


@app.get("/api/backtest/datasets/{dataset_id}/signals")
async def backtest_dataset_signals(
    dataset_id: int, limit: int = Query(100, ge=1, le=1000), strategy: str = Query("smart-money")
) -> dict[str, object]:
    settings = get_settings()
    async with session_scope(settings) as session:
        dataset, rows = await _dataset_or_404(dataset_id, session)
        result = run_backtest(rows, strategy=strategy)
        await _assert_dataset_current(session, dataset.id)
    return {
        "status": result.status,
        "model_version": result.model_version,
        "dataset_id": dataset.id,
        "dataset_version": dataset.dataset_version,
        **_snapshot_provenance(dataset),
        "signals": [signal.as_dict() for signal in result.signals[-limit:]],
        "count": len(result.signals),
    }


@app.get("/api/bsc/market-events")
async def bsc_market_events(
    limit: int = Query(50, ge=1, le=200), token: str | None = Query(None)
) -> dict[str, object]:
    """Return normalized chain events; DexScreener remains enrichment only."""

    settings = get_settings()
    async with session_scope(settings) as session:
        query = select(MarketEvent).where(MarketEvent.chain_id == settings.bsc_chain_id)
        if token:
            query = query.where(MarketEvent.token_address == token)
        rows = list(
            (
                await session.scalars(query.order_by(MarketEvent.block_number.desc()).limit(limit))
            ).all()
        )
    return {
        "chain": "bsc",
        "source": "chain_discovery",
        "source_priority": ["chain_discovery", "dexscreener_enrichment"],
        "events": [
            {
                "event_type": row.event_type,
                "token": row.token_address,
                "pair": row.pair_address,
                "wallet": row.wallet_address,
                "tx_hash": row.tx_hash,
                "block": row.block_number,
                "timestamp": row.timestamp.isoformat(),
                "side": row.side,
                "token_amount": str(row.token_amount) if row.token_amount is not None else None,
                "quote_amount": str(row.quote_amount) if row.quote_amount is not None else None,
                "usd_value": str(row.usd_value) if row.usd_value is not None else None,
                "price": str(row.price) if row.price is not None else None,
                "source": row.source,
                "confidence": str(row.confidence) if row.confidence is not None else None,
                "metadata": row.metadata_json,
            }
            for row in rows
        ],
    }


@app.get("/api/radar/tokens/{token}/early-buyers")
async def token_early_buyers(
    token: str,
    limit: int = Query(20, ge=1, le=200),
    sort: str = Query("rank"),
    time_window: str | None = Query(None, description="buyers:20, blocks:50, or minutes:5"),
    as_of: datetime | None = Query(None),  # noqa: B008
) -> dict[str, object]:
    """Return deterministic early buyers and their observed positions/PnL."""
    settings = get_settings()
    token = _validated_address(token)
    window_type, window_value = settings.early_buyer_window_type, settings.early_buyer_window_value
    if time_window:
        parts = time_window.lower().split(":", 1)
        if (
            parts[0] not in {"buyers", "blocks", "minutes"}
            or len(parts) != 2
            or not parts[1].isdigit()
            or int(parts[1]) <= 0
        ):
            raise HTTPException(
                status_code=400,
                detail="time_window 必须是 buyers:N、blocks:N 或 minutes:N",
            )
        window_type, window_value = parts[0], int(parts[1])
    cutoff = _cutoff(as_of or datetime.now(UTC))
    if cutoff > datetime.now(UTC):
        raise HTTPException(400, "as_of cannot be in the future")
    async with session_scope(settings) as session:
        try:
            inputs = await session.run_sync(lambda sync: load_replay_inputs(sync, token, as_of=cutoff))
        except StaleReplayInputs as exc:
            raise HTTPException(503, "canonical inputs changed; retry the early-buyer read") from exc
    if inputs.pair is None:
        return {"token": token, "early_buyers": [], "data_status": "UNKNOWN_TOKEN_CREATION",
                **_freshness_fields(None, None)}
    config = EarlyBuyerConfig(
        top_n=limit, max_entry_hours=settings.early_buyer_max_entry_hours,
        window_type=window_type, window_value=window_value,
    )
    buyers = inputs.buyers(config=config)
    if sort == "entry_usd":
        buyers.sort(key=lambda b: b.first_buy_usd or 0, reverse=True)
    elif sort == "pnl":
        buyers.sort(key=lambda b: b.realized_pnl or 0, reverse=True)
    observed_at = max(
        (_cutoff(row["timestamp"]) for row in inputs.transactions),
        default=datetime.fromisoformat(inputs.pair["creation_timestamp"]),
    )
    return {
        "token": token,
        "window": {"type": window_type, "value": window_value or limit},
        "data_status": "CHAIN_EVENTS" if buyers else "NO_CANONICAL_EARLY_BUYERS",
        "replay": {"as_of": cutoff.isoformat(), "source_epoch": inputs.source_epoch,
                   "digest": inputs.input_manifest(config=config)["digest"]},
        **_freshness_fields(observed_at, observed_at),
        "early_buyers": [
            {
                "wallet": b.wallet_address,
                "rank": b.buy_rank,
                "entry_time": b.first_buy_time.isoformat(),
                "entry_delay_seconds": b.entry_delay_seconds,
                "entry_time_bucket": b.entry_time_bucket,
                "entry_price": str(b.first_buy_price) if b.first_buy_price is not None else None,
                "entry_usd": str(b.first_buy_usd) if b.first_buy_usd is not None else None,
                "entry_market_cap": str(b.first_buy_market_cap)
                if b.first_buy_market_cap is not None
                else None,
                "market_cap_status": b.market_cap_status,
                "current_position": str(b.current_balance)
                if b.current_balance is not None
                else None,
                "realized_pnl": str(b.realized_pnl) if b.realized_pnl is not None else None,
                "unrealized_pnl": str(b.unrealized_pnl) if b.unrealized_pnl is not None else None,
                "roi": str(b.roi) if b.roi is not None else None,
                "total_invested": str(b.total_invested) if b.total_invested is not None else None,
                "total_returned": str(b.total_returned) if b.total_returned is not None else None,
                "buy_count": b.buy_count,
                "sell_count": b.sell_count,
            }
            for b in buyers
        ],
    }


@app.get("/api/wallet/{address}/profile")
async def wallet_profile(
    address: str,
    chain: str = Query(DEFAULT_CHAIN),
    as_of: datetime | None = Query(None),  # noqa: B008
    score_version: str = Query(SCORE_VERSION),
) -> dict[str, object]:
    """Return the latest materialized profile not newer than the requested cutoff."""
    chain = _validated_chain(chain)
    address = _validated_address(address)
    settings = get_settings()
    async with session_scope(settings) as session:
        row = await session.scalar(
            select(WalletSmartMoneySnapshot)
            .where(
                WalletSmartMoneySnapshot.chain == chain,
                WalletSmartMoneySnapshot.wallet_address == address,
                WalletSmartMoneySnapshot.score_version == score_version,
                WalletSmartMoneySnapshot.as_of <= _cutoff(as_of),
            )
            .order_by(WalletSmartMoneySnapshot.as_of.desc())
            .limit(1)
        )
    if row is None:
        raise HTTPException(status_code=404, detail="wallet snapshot not found at as_of")
    # Network influence is derived from the wallet's token neighbourhood, not
    # just its own wallet-token activity. The profile snapshot itself remains
    # point-in-time; this graph is independently cut off at the same instant.
    trades = await _wallet_graph_trade_rows(address, chain, as_of)
    graph = build_wallet_knowledge_graph(
        trades,
        chain=chain,
        as_of=as_of,
        max_total_nodes=get_settings().graph_max_total_nodes,
        max_wallet_nodes=get_settings().graph_max_wallet_nodes,
        max_token_nodes=get_settings().graph_max_token_nodes,
        max_trade_nodes=get_settings().graph_max_trade_nodes,
        max_graph_edges=get_settings().graph_max_edges,
        max_source_events=get_settings().graph_max_source_events,
        max_pair_checks=get_settings().graph_max_pair_checks,
    )
    network_kinds = {
        "funded_wallet",
        "funded_by",
        "traded_together",
        "synchronized_entry",
        "coordinated_exit",
        "repeated_co_buy",
        "followed_wallet",
    }
    network_edges = sum(
        1
        for relationship in graph.relationships_for(address)
        if relationship.kind in network_kinds
        and normalize_address(relationship.source, chain="bsc")
        != normalize_address(relationship.target, chain="bsc")
    )
    network_influence = min(Decimal(1), Decimal(network_edges) / Decimal(10))
    return _wallet_snapshot_payload(row, network_influence=network_influence)


async def _wallet_trade_rows(
    address: str, chain: str, as_of: datetime | None = None
) -> list[dict[str, object]]:
    chain = _validated_chain(chain)
    address = _validated_address(address)
    settings = get_settings()
    cutoff = _cutoff(as_of)
    async with session_scope(settings) as session:
        rows = list(
            (
                await session.scalars(
                    select(TradeEvent)
                    .where(
                        TradeEvent.chain == chain,
                        TradeEvent.trader_address == address,
                        TradeEvent.timestamp <= cutoff,
                    )
                    .order_by(TradeEvent.timestamp, TradeEvent.id)
                )
            ).all()
        )
    return [_trade_payload(row) for row in rows]


async def _wallet_graph_trade_rows(
    address: str,
    chain: str,
    as_of: datetime | None = None,
    *,
    max_events: int = 20_000,
    return_truncated: bool = False,
) -> list[dict[str, object]] | tuple[list[dict[str, object]], bool]:
    chain = _validated_chain(chain)
    address = _validated_address(address)
    settings = get_settings()
    cutoff = _cutoff(as_of)
    async with session_scope(settings) as session:
        own = list(
            (
                await session.scalars(
                    select(TradeEvent)
                    .where(
                        TradeEvent.chain == chain,
                        TradeEvent.trader_address == address,
                        TradeEvent.timestamp <= cutoff,
                    )
                    .order_by(TradeEvent.timestamp, TradeEvent.id)
                    .limit(max_events + 1)
                )
            ).all()
        )
        source_truncated = len(own) > max_events
        own = own[:max_events]
        tokens = {row.token_address for row in own}
        if not tokens:
            result: list[dict[str, object]] = []
            return (result, source_truncated) if return_truncated else result
        rows = list(
            (
                await session.scalars(
                    select(TradeEvent)
                    .where(
                        TradeEvent.chain == chain,
                        TradeEvent.token_address.in_(tokens),
                        TradeEvent.timestamp <= cutoff,
                    )
                    .order_by(TradeEvent.timestamp, TradeEvent.id)
                    .limit(max_events + 1)
                )
            ).all()
        )
        source_truncated = source_truncated or len(rows) > max_events
        rows = rows[:max_events]
    result = [_trade_payload(row) for row in rows]
    return (result, source_truncated) if return_truncated else result


@app.get("/api/wallet/{address}/clusters")
async def wallet_clusters(
    address: str,
    chain: str = Query(DEFAULT_CHAIN),
    as_of: datetime | None = Query(None),  # noqa: B008
) -> dict[str, object]:
    """Return deterministic clusters and graph edges observed by ``as_of``."""
    chain = _validated_chain(chain)
    address = _validated_address(address)
    trades, source_truncated = await _wallet_graph_trade_rows(
        address, chain, as_of, return_truncated=True
    )
    settings = get_settings()
    graph = build_wallet_knowledge_graph(
        trades,
        chain=chain,
        as_of=as_of,
        max_total_nodes=settings.graph_max_total_nodes,
        max_wallet_nodes=settings.graph_max_wallet_nodes,
        max_token_nodes=settings.graph_max_token_nodes,
        max_trade_nodes=settings.graph_max_trade_nodes,
        max_graph_edges=settings.graph_max_edges,
        max_source_events=settings.graph_max_source_events,
        max_pair_checks=settings.graph_max_pair_checks,
    )
    clusters = detect_wallet_clusters(graph)
    selected = [cluster for cluster in clusters if address in cluster.wallets]
    relationships = graph.relationships_for(address)
    return {
        "wallet": address,
        "chain": chain,
        "as_of": _cutoff(as_of),
        "clusters": [
            {
                "cluster_id": item.cluster_id,
                "wallets": list(item.wallets),
                "evidence": item.evidence,
                "confidence": item.confidence,
            }
            for item in selected
        ],
        "relationships": [
            {
                "source": item.source,
                "target": item.target,
                "kind": item.kind,
                "weight": item.weight,
                "evidence": item.evidence,
            }
            for item in relationships
        ],
        "data_status": "OBSERVED" if trades else "UNKNOWN",
        "truncated": graph.truncated or source_truncated,
        "truncation_reasons": list(graph.truncation_reasons)
        + (["source_event_limit"] if source_truncated else []),
    }


@app.get("/api/wallet/{address}/graph")
async def wallet_graph(
    address: str,
    chain: str = Query(DEFAULT_CHAIN),
    as_of: datetime | None = Query(None),  # noqa: B008
) -> dict[str, object]:
    """Return the wallet-centric knowledge graph for UI rendering."""
    chain = _validated_chain(chain)
    address = _validated_address(address)
    trades, source_truncated = await _wallet_graph_trade_rows(
        address, chain, as_of, return_truncated=True
    )
    settings = get_settings()
    graph = build_wallet_knowledge_graph(
        trades,
        chain=chain,
        as_of=as_of,
        max_total_nodes=settings.graph_max_total_nodes,
        max_wallet_nodes=settings.graph_max_wallet_nodes,
        max_token_nodes=settings.graph_max_token_nodes,
        max_trade_nodes=settings.graph_max_trade_nodes,
        max_graph_edges=settings.graph_max_edges,
        max_source_events=settings.graph_max_source_events,
        max_pair_checks=settings.graph_max_pair_checks,
    )
    graph_clusters = detect_wallet_clusters(graph)
    node_rows = (
        [{"id": item.address, "type": "wallet", "chain": item.chain} for item in graph.wallets]
        + [{"id": item.address, "type": "token", "chain": item.chain} for item in graph.tokens]
        + [
            {
                "id": f"trade:{index}",
                "type": "trade",
                "wallet": item.wallet,
                "token": item.token,
                "side": item.side,
                "timestamp": item.timestamp,
                "value": item.value,
            }
            for index, item in enumerate(graph.trades)
        ]
        + [
            {
                "id": item.cluster_id,
                "type": "cluster",
                "wallets": list(item.wallets),
                "confidence": item.confidence,
            }
            for item in graph_clusters[: settings.graph_max_cluster_nodes]
        ]
    )
    node_rows_truncated = len(node_rows) > settings.graph_max_total_nodes
    if node_rows_truncated:
        node_rows = node_rows[: settings.graph_max_total_nodes]
    truncation_reasons = list(graph.truncation_reasons)
    if source_truncated:
        truncation_reasons.append("source_event_limit")
    if node_rows_truncated:
        truncation_reasons.append("total_node_limit")
    return {
        "wallet": address,
        "chain": chain,
        "as_of": _cutoff(as_of),
        "nodes": node_rows,
        "edges": [
            {
                "source": item.source,
                "target": item.target,
                "kind": item.kind,
                "weight": item.weight,
                "evidence": item.evidence,
            }
            for item in graph.relationships[: settings.graph_max_edges]
        ],
        "truncated": graph.truncated or source_truncated or node_rows_truncated,
        "truncation": {
            **graph.truncation,
            **({"source_event_limit": 20_000} if source_truncated else {}),
        },
        "truncation_reasons": truncation_reasons,
        "data_status": "OBSERVED" if trades else "UNKNOWN",
    }


@app.get("/api/token/{address}/smart-money-flow")
async def token_smart_money_flow(
    address: str,
    window: str = Query("24h"),
    chain: str = Query(DEFAULT_CHAIN),
    as_of: datetime | None = Query(None),  # noqa: B008
) -> dict[str, object]:
    """Return smart-money inflow/outflow for a bounded time window."""
    chain = _validated_chain(chain)
    address = _validated_address(address)
    try:
        window_seconds = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "24h": 86400}[window]
    except KeyError as exc:
        raise HTTPException(status_code=400, detail="window 必须是 1m、5m、15m、1h 或 24h") from exc
    settings = get_settings()
    cutoff = _cutoff(as_of)
    start = cutoff - timedelta(seconds=window_seconds)
    async with session_scope(settings) as session:
        rows = list(
            (
                await session.scalars(
                    select(TradeEvent)
                    .where(
                        TradeEvent.chain == chain,
                        TradeEvent.token_address == address,
                        TradeEvent.timestamp >= start,
                        TradeEvent.timestamp <= cutoff,
                    )
                    .order_by(TradeEvent.timestamp, TradeEvent.id)
                    .limit(20_001)
                )
            ).all()
        )
        flow_truncated = len(rows) > 20_000
        snapshot = aliased(WalletSmartMoneySnapshot)
        latest_snapshot = (
            select(func.max(snapshot.as_of))
            .where(
                snapshot.chain == WalletSmartMoneySnapshot.chain,
                snapshot.wallet_address == WalletSmartMoneySnapshot.wallet_address,
                snapshot.score_version == WalletSmartMoneySnapshot.score_version,
                snapshot.as_of <= cutoff,
            )
            .correlate(WalletSmartMoneySnapshot)
            .scalar_subquery()
        )
        wallets = list(
            (
                await session.scalars(
                    select(WalletSmartMoneySnapshot.wallet_address)
                    .where(
                        WalletSmartMoneySnapshot.chain == chain,
                        WalletSmartMoneySnapshot.score_version == SCORE_VERSION,
                        WalletSmartMoneySnapshot.as_of == latest_snapshot,
                        WalletSmartMoneySnapshot.score >= Decimal(60),
                    )
                    .distinct()
                )
            ).all()
        )
    try:
        result = smart_money_flow(
            [_trade_payload(row) for row in rows[:20_000]],
            token=address,
            smart_wallets=wallets,
            window=window,
            as_of=cutoff,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    result["truncated"] = flow_truncated
    result["max_trades"] = 20_000
    return result


@app.post("/api/wallet/replay")
async def wallet_replay(payload: dict[str, object]) -> dict[str, object]:
    """Replay successful meme tokens and identify recurring early wallets."""
    settings = get_settings()
    chain = _validated_chain(str(payload.get("chain", DEFAULT_CHAIN)))
    as_of = payload.get("as_of")
    try:
        cutoff = _cutoff(datetime.fromisoformat(str(as_of))) if as_of else _cutoff(None)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="as_of 必须是 ISO-8601 格式") from exc
    token_rows = payload.get("successful_tokens", [])
    if not isinstance(token_rows, list):
        raise HTTPException(status_code=400, detail="successful_tokens 必须是列表")
    try:
        max_days = min(30, max(1, int(payload.get("max_days", 30))))
        max_trades = min(20_000, max(1, int(payload.get("max_trades", 10_000))))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="max_days 和 max_trades 必须是正整数") from exc
    token_addresses = [
        _validated_address(str(item.get("address", item.get("token_address", ""))))
        for item in token_rows
        if isinstance(item, dict)
    ]
    start = cutoff - timedelta(days=max_days)
    async with session_scope(settings) as session:
        query = select(TradeEvent).where(
            TradeEvent.chain == chain,
            TradeEvent.timestamp >= start,
            TradeEvent.timestamp <= cutoff,
        )
        if token_addresses:
            query = query.where(TradeEvent.token_address.in_(token_addresses))
        rows = list(
            (
                await session.scalars(
                    query.order_by(TradeEvent.timestamp, TradeEvent.id).limit(max_trades + 1)
                )
            ).all()
        )
    truncated = len(rows) > max_trades
    result = replay_historical_smart_money(
        token_rows, [_trade_payload(row) for row in rows[:max_trades]], as_of=cutoff
    )
    result.update({"truncated": truncated, "max_days": max_days, "max_trades": max_trades})
    return result


@app.get("/api/radar/smart-money/wallets")
async def smart_money_wallets(
    chain: str = Query("bsc"),
    limit: int = Query(100, ge=1, le=500),
    sort: str = Query("score"),
    min_sample: int = Query(0, ge=0),
    min_score: Decimal = Query(Decimal(0), ge=0, le=100),  # noqa: B008
    as_of: datetime | None = Query(None),  # noqa: B008
    score_version: str = Query(SCORE_VERSION),
) -> dict[str, object]:
    """Rank versioned wallet snapshots without replaying the trade stream."""
    allowed_sort = {"score", "roi", "win_rate", "sample_size", "profit_factor"}
    chain = _validated_chain(chain)
    if sort not in allowed_sort:
        raise HTTPException(
            status_code=400, detail=f"sort 必须是以下值之一：{sorted(allowed_sort)}"
        )
    settings = get_settings()
    async with session_scope(settings) as session:
        candidate = aliased(WalletSmartMoneySnapshot)
        latest_as_of = (
            select(func.max(candidate.as_of))
            .where(
                candidate.chain == WalletSmartMoneySnapshot.chain,
                candidate.wallet_address == WalletSmartMoneySnapshot.wallet_address,
                candidate.score_version == WalletSmartMoneySnapshot.score_version,
                candidate.as_of <= _cutoff(as_of),
            )
            .correlate(WalletSmartMoneySnapshot)
            .scalar_subquery()
        )
        query = select(WalletSmartMoneySnapshot).where(
            WalletSmartMoneySnapshot.chain == chain,
            WalletSmartMoneySnapshot.score_version == score_version,
            WalletSmartMoneySnapshot.as_of == latest_as_of,
            WalletSmartMoneySnapshot.sample_size >= min_sample,
            WalletSmartMoneySnapshot.score >= min_score,
        )
        if sort == "score":
            order_column = WalletSmartMoneySnapshot.score
        elif sort == "sample_size":
            order_column = WalletSmartMoneySnapshot.sample_size
        else:
            # Profile metrics are materialized JSON fields. Keep cast/order/
            # limit in PostgreSQL so a large wallet table never enters Python.
            order_column = func.cast(
                    WalletSmartMoneySnapshot.profile_json[sort].as_string(),
                    Numeric(38, 18),
            )
        query = query.order_by(
            order_column.desc().nulls_last(), WalletSmartMoneySnapshot.wallet_address.desc()
        ).limit(limit).execution_options(populate_existing=True)
        rows = list((await session.scalars(query)).all())
    wallets = [_wallet_snapshot_payload(row) for row in rows]
    return {"chain": chain, "wallets": wallets, "count": len(wallets)}


@app.get("/api/radar/tokens/{token}/smart-money")
async def token_smart_money(
    token: str,
    limit: int = Query(100, ge=1, le=500),
    as_of: datetime | None = Query(None),  # noqa: B008
    score_version: str = Query(SCORE_VERSION),
) -> dict[str, object]:
    """Return the latest token snapshot not newer than the requested cutoff."""
    settings = get_settings()
    token = _validated_address(token)
    async with session_scope(settings) as session:
        row = await session.scalar(
            select(TokenSmartMoneySnapshot)
            .where(
                TokenSmartMoneySnapshot.chain == "bsc",
                TokenSmartMoneySnapshot.token_address == token,
                TokenSmartMoneySnapshot.score_version == score_version,
                TokenSmartMoneySnapshot.as_of <= _cutoff(as_of),
            )
            .order_by(TokenSmartMoneySnapshot.as_of.desc())
            .limit(1)
        )
    if row is None:
        return {
            "token": token,
            "data_status": "UNKNOWN",
            "smart_money": None,
            **_freshness_fields(None, None),
        }
    payload = _token_snapshot_payload(row)
    payload["top_smart_wallets"] = payload["top_smart_wallets"][:limit]
    return {"token": token, "data_status": row.data_status, "smart_money": payload}


@app.get("/api/radar/tokens/{token}/risk")
async def token_risk(
    token: str,
    as_of: datetime | None = Query(None),  # noqa: B008
    risk_version: str = Query(RISK_VERSION),
) -> dict[str, object]:
    """Return the latest materialized risk snapshot at or before a cutoff."""
    settings = get_settings()
    token = _validated_address(token)
    async with session_scope(settings) as session:
        row = await session.scalar(
            select(TokenRiskSnapshot)
            .where(
                TokenRiskSnapshot.chain == "bsc",
                TokenRiskSnapshot.token_address == token,
                TokenRiskSnapshot.risk_version == risk_version,
                TokenRiskSnapshot.as_of <= _cutoff(as_of),
            )
            .order_by(TokenRiskSnapshot.as_of.desc())
            .limit(1)
        )
    if row is None:
        return {
            "token": token,
            "data_status": "UNKNOWN",
            "risk": None,
            **_freshness_fields(None, None),
        }
    return {"token": token, "data_status": "MATERIALIZED", "risk": _risk_snapshot_payload(row)}


@app.get("/api/radar/risk/tokens")
async def risk_tokens(
    chain: str = Query("bsc"),
    limit: int = Query(100, ge=1, le=500),
    as_of: datetime | None = Query(None),  # noqa: B008
    risk_version: str = Query(RISK_VERSION),
) -> dict[str, object]:
    """List latest risk snapshots; no analysis is performed at request time."""
    chain = _validated_chain(chain)
    settings = get_settings()
    cutoff = _cutoff(as_of)
    async with session_scope(settings) as session:
        rows = list(
            (
                await session.scalars(
                    select(TokenRiskSnapshot)
                    .where(
                        TokenRiskSnapshot.chain == chain,
                        TokenRiskSnapshot.risk_version == risk_version,
                        TokenRiskSnapshot.as_of <= cutoff,
                    )
                    .order_by(TokenRiskSnapshot.as_of.desc())
                    .limit(limit * 4)
                )
            ).all()
        )
    latest: dict[str, TokenRiskSnapshot] = {}
    for row in rows:
        latest.setdefault(row.token_address, row)
    values = list(latest.values())[:limit]
    return {
        "chain": chain,
        "risk_version": risk_version,
        "tokens": [_risk_snapshot_payload(row) for row in values],
        "count": len(values),
    }


@app.get("/health")
async def health() -> dict[str, object]:
    settings = get_settings()
    database = await check_database(settings)
    redis = await check_redis(settings)
    audit_metrics: dict[str, object] = {
        "queue_depth": 0,
        "oldest_pending_age_seconds": 0,
        "running_jobs": 0,
        "success_count": 0,
        "retry_count": 0,
        "failure_count": 0,
        "processing_latency_ms": 0,
    }
    worker_health: dict[str, dict[str, object]] = {}
    runtime_status: RuntimeWorkerStatus | None = None
    stream_checkpoint: StreamCheckpoint | None = None
    if database == "ok":
        try:
            async with session_scope(settings) as session:
                audit_metrics = await session.run_sync(lambda sync: AuditQueue(sync).metrics())
                def read_worker_state(sync):
                    return (
                        _worker_health(sync, now=datetime.now(UTC)),
                        sync.scalar(
                            select(RuntimeWorkerStatus).where(
                                RuntimeWorkerStatus.worker_name == "bsc-live-tail"
                            )
                        )
                        or sync.scalar(
                            select(RuntimeWorkerStatus).where(
                                RuntimeWorkerStatus.worker_name == "bsc-ingestion"
                            )
                        ),
                        sync.scalar(
                            select(StreamCheckpoint).where(
                                StreamCheckpoint.chain_id == BSC_CHAIN_ID,
                                StreamCheckpoint.stream_name == settings.bsc_live_tail_stream_name,
                            )
                        )
                        or sync.scalar(
                            select(StreamCheckpoint).where(
                                StreamCheckpoint.chain_id == BSC_CHAIN_ID,
                                StreamCheckpoint.stream_name == "bsc_realtime",
                            )
                        ),
                    )
                worker_health, runtime_status, stream_checkpoint = await session.run_sync(
                    read_worker_state
                )
        except (SQLAlchemyError, OSError, RuntimeError):
            audit_metrics = {"status": "unavailable"}
    if database == "ok":
        source_health_registry.record_success("postgresql")
    else:
        source_health_registry.record_failure("postgresql", "database unavailable")
    if redis == "ok":
        source_health_registry.record_success("redis")
    else:
        source_health_registry.record_failure("redis", "redis unavailable")

    # Configuration is reported separately from liveness. A configured RPC or
    # provider is UNKNOWN until an adapter records a successful request.
    source_health_registry.register(
        "bsc_websocket",
        enabled=bool(settings.bsc_ws_endpoints),
        details={
            "configured": bool(settings.bsc_ws_endpoints),
            "configured_provider_count": len(settings.bsc_ws_endpoints),
        },
    )
    source_health_registry.register(
        "bsc_http",
        enabled=bool(settings.bsc_rpc_endpoints),
        details={"configured": bool(settings.bsc_rpc_endpoints)},
    )
    for source, state in _persisted_bsc_health(
        runtime_status,
        stream_checkpoint,
        ws_configured=bool(settings.bsc_ws_endpoints),
        http_configured=bool(settings.bsc_rpc_endpoints),
        now=datetime.now(UTC),
    ).items():
        source_health_registry.set_status(source, state["status"], details=state["details"])
    source_health_registry.register(
        "dexscreener",
        enabled=bool(settings.external_evidence_enabled and settings.dexscreener_enabled),
        details={
            "configured": bool(settings.external_evidence_enabled and settings.dexscreener_enabled)
        },
    )
    source_health_registry.register(
        "goplus",
        enabled=bool(
            settings.external_evidence_enabled
            and settings.goplus_enabled
            and settings.goplus_auth_configured
        ),
        details={
            "configured": bool(settings.external_evidence_enabled and settings.goplus_enabled),
            "auth_configured": settings.goplus_auth_configured,
            "reason": None if settings.goplus_auth_configured else "APP_KEY_SECRET_REQUIRED",
        },
    )
    flap = _flap_readiness()
    source_health_registry.register(
        "flap",
        enabled=bool(flap["flap_ready"]),
        details=flap,
    )
    source_health_registry.set_status(
        "flap", "HEALTHY" if flap["flap_ready"] else "UNKNOWN", details=flap
    )
    metrics.record_sse_client_count(event_bus.subscriber_count)
    if event_bus.subscriber_count:
        source_health_registry.record_success(
            "sse", details={"client_count": event_bus.subscriber_count}
        )
    else:
        source_health_registry.set_status("sse", "UNKNOWN", details={"client_count": 0})
    if isinstance(audit_metrics, dict) and audit_metrics.get("status") == "unavailable":
        source_health_registry.record_failure("audit_queue", "audit queue unavailable")
    elif isinstance(audit_metrics, dict):
        metrics.set_gauge("audit_queue_depth", int(audit_metrics.get("queue_depth", 0) or 0))

    for source, health_row in worker_health.items():
        source_health_registry.register(source, enabled=True, details=health_row)
        source_health_registry.set_status(source, str(health_row["status"]), details=health_row)

    snapshot = observability.snapshot()
    legacy_status = "ok" if database == "ok" and redis == "ok" else "degraded"
    return {
        "status": legacy_status,
        "database": database,
        "redis": redis,
        "bsc_rpc": "ok" if settings.bsc_rpc_endpoints else "not_configured",
        "bsc_stream": "ready"
        if (settings.bsc_ws_endpoints or settings.bsc_rpc_endpoints)
        else "not_configured",
        "market_feed": "ready",
        "market_provider": "ok" if settings.market_api_key else "not_configured",
        "trading_mode": settings.trading_mode,
        "live_trading_enabled": settings.enable_live_trading,
        **flap,
        "radar_audit": audit_metrics,
        "workers": worker_health,
        "sources": snapshot["sources"],
        "source_health": snapshot["sources"],
        "metrics": snapshot["metrics"],
        "observability": snapshot,
    }


def _worker_health(sync, *, now: datetime) -> dict[str, dict[str, object]]:
    """Translate durable worker heartbeats into secret-free source health."""
    worker_types = {
        "bsc-ingestion": "bsc_ingestion",
        "bsc-market-enrichment": "bsc_market_enrichment",
        "radar-worker": "radar_worker",
        "forward-worker": "forward_worker",
    }
    # A worker type can have rows from stopped/restarted instances.  Always
    # resolve the newest durable heartbeat so an old instance cannot mask the
    # currently running worker.
    rows: dict[str, WorkerHeartbeat] = {}
    for row in sync.scalars(
        select(WorkerHeartbeat).order_by(
            WorkerHeartbeat.worker_type,
            WorkerHeartbeat.last_heartbeat_at.desc(),
            WorkerHeartbeat.id.desc(),
        )
    ).all():
        if row.worker_type in worker_types and row.worker_type not in rows:
            rows[row.worker_type] = row
    result: dict[str, dict[str, object]] = {}
    for worker_type, source in worker_types.items():
        row = rows.get(worker_type)
        if row is None:
            result[source] = {
                "worker_type": worker_type,
                "status": "DOWN",
                "reason": "NO_HEARTBEAT",
                "last_heartbeat_at": None,
            }
            continue
        heartbeat_at = row.last_heartbeat_at
        if heartbeat_at.tzinfo is None:
            heartbeat_at = heartbeat_at.replace(tzinfo=UTC)
        age = max(0.0, (now - heartbeat_at).total_seconds())
        if age >= 300:
            status = "DOWN"
        elif age >= 90:
            status = "STALE"
        else:
            status = str(row.status or "UNKNOWN").upper()
            if status == "RUNNING":
                status = "HEALTHY"
        result[source] = {
            "worker_type": worker_type,
            "status": status,
            "last_heartbeat_at": heartbeat_at,
            "age_seconds": age,
            "current_job": row.current_job,
        }
    return result


def _persisted_bsc_health(
    runtime_status: RuntimeWorkerStatus | None,
    stream_checkpoint: StreamCheckpoint | None,
    *,
    ws_configured: bool,
    http_configured: bool,
    now: datetime,
) -> dict[str, dict[str, Any]]:
    """Project canonical worker/checkpoint state into transport health."""
    updated_at = getattr(runtime_status, "updated_at", None) or getattr(
        stream_checkpoint, "updated_at", None
    )
    if updated_at is not None and updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=UTC)
    age = (now - updated_at).total_seconds() if updated_at is not None else None
    stale = age is None or age >= 90
    worker_state = str(getattr(runtime_status, "status", "UNKNOWN") or "UNKNOWN").upper()
    active_transport = str(getattr(runtime_status, "transport", "") or "").lower()
    connected = bool(
        runtime_status is not None
        and not stale
        and worker_state in {"RUNNING", "IDLE_HEALTHY", "CATCHING_UP", "LIVE"}
    )
    ws_active = connected and active_transport in {"ws", "websocket"}
    http_active = connected and active_transport in {"http", "http_polling", "http_fallback"}
    degraded = bool(runtime_status is not None and worker_state in {"DEGRADED", "FAILED"})
    primary = "websocket" if ws_active else "http" if http_active else None
    fallback = "http" if ws_configured and http_configured and ws_active else "websocket" if ws_configured and http_configured and http_active else None

    def state(configured: bool, active: bool) -> str:
        if not configured:
            return "UNKNOWN"
        if stale:
            return "STALE"
        if degraded:
            return "DEGRADED"
        return "HEALTHY" if active else "UNKNOWN"

    def details(*, configured: bool, role: str, active: bool) -> dict[str, Any]:
        head_block = getattr(runtime_status, "head_block", None) or getattr(runtime_status, "last_block", None)
        checkpoint_block = getattr(runtime_status, "checkpoint_block", None)
        block_lag = getattr(runtime_status, "block_lag", None)
        if block_lag is None:
            block_lag = getattr(runtime_status, "lag", None)
        if block_lag is None and head_block is not None and checkpoint_block is not None:
            block_lag = max(0, int(head_block) - int(checkpoint_block))
        live_ready = bool(getattr(runtime_status, "live_ready", False) and active and not stale)
        phase = getattr(runtime_status, "ingestion_phase", None) or (
            "LIVE" if live_ready else "CATCHING_UP" if active else "UNKNOWN"
        )
        return {
            "configured": configured,
            "connected": active,
            "role": role,
            "primary_transport": primary,
            "fallback_transport": fallback,
            "active_transport": active_transport or None,
            "active_provider": getattr(runtime_status, "provider", None),
            "worker_status": worker_state,
            "degraded": degraded,
            "stale": stale,
            "checkpoint_block": getattr(stream_checkpoint, "last_processed_block", None),
            "head_block": head_block,
            "block_lag": block_lag,
            "ingestion_phase": phase,
            "live_ready": live_ready,
            "state_age_seconds": round(age, 2) if age is not None else None,
        }

    return {
        "bsc_websocket": {
            "status": state(ws_configured, ws_active),
            "details": details(
                configured=ws_configured,
                role="primary" if ws_active else "candidate",
                active=ws_active,
            ),
        },
        "bsc_http": {
            "status": state(http_configured, http_active),
            "details": details(
                configured=http_configured,
                role="primary" if http_active else "fallback",
                active=http_active,
            ),
        },
    }


@app.get("/diagnostics")
async def diagnostics() -> dict[str, object]:
    """Operational diagnostics with bounded, secret-free source metadata."""

    return await health()


@app.get("/api/radar/diagnostics")
async def radar_diagnostics() -> dict[str, object]:
    return await health()


@app.get("/api/bsc/stream/status")
async def bsc_stream_status() -> dict[str, object]:
    settings = get_settings()
    if not settings.bsc_ws_endpoints and not settings.bsc_rpc_endpoints:
        source_health_registry.set_status("bsc_websocket", "UNKNOWN", details={"configured": False})
        source_health_registry.set_status("bsc_http", "UNKNOWN", details={"configured": False})
        payload = {
            "chain_id": settings.bsc_chain_id,
            "provider": "not_configured",
            "stream": "bsc_logs",
            "status": "not_configured",
            "current_block": None,
            "last_processed_block": None,
            "block_lag": None,
            "event_rate": 0,
            "reconnect_count": 0,
            "provider_errors": 0,
            "ingestion_latency": None,
        }
        payload["metrics"] = observability.metrics.snapshot()
        return payload
    async with session_scope(settings) as session:
        def read(sync):
            checkpoint = sync.scalar(
                select(StreamCheckpoint).where(
                    StreamCheckpoint.chain_id == BSC_CHAIN_ID,
                    StreamCheckpoint.stream_name == settings.bsc_live_tail_stream_name,
                ).order_by(StreamCheckpoint.updated_at.desc())
            ) or sync.scalar(
                select(StreamCheckpoint).where(
                    StreamCheckpoint.chain_id == BSC_CHAIN_ID,
                    StreamCheckpoint.stream_name == "bsc_realtime",
                ).order_by(StreamCheckpoint.updated_at.desc())
            )
            worker = sync.scalar(
                select(WorkerHeartbeat).where(WorkerHeartbeat.worker_type == "bsc-live-tail")
                .order_by(WorkerHeartbeat.last_heartbeat_at.desc())
            ) or sync.scalar(
                select(WorkerHeartbeat).where(WorkerHeartbeat.worker_type == "bsc-ingestion")
                .order_by(WorkerHeartbeat.last_heartbeat_at.desc())
            )
            shared = sync.scalar(
                select(RuntimeWorkerStatus).where(RuntimeWorkerStatus.worker_name == "bsc-live-tail")
            ) or sync.scalar(
                select(RuntimeWorkerStatus).where(RuntimeWorkerStatus.worker_name == "bsc-ingestion")
            )
            return checkpoint, worker, shared
        checkpoint, worker, shared = await session.run_sync(read)
    now = datetime.now(UTC)
    heartbeat_age = (now - shared.updated_at).total_seconds() if shared else ((now - worker.last_heartbeat_at).total_seconds() if worker else None)
    payload = {
        "chain_id": BSC_CHAIN_ID,
        "provider": settings.bsc_provider_name,
        "stream": "bsc_realtime",
        "status": str(shared.status if shared else worker.status if worker else "unknown").lower(),
        "current_block": shared.head_block if shared and shared.head_block is not None else shared.last_block if shared else None,
        "last_processed_block": checkpoint.last_processed_block if checkpoint else None,
        "block_lag": shared.block_lag if shared and shared.block_lag is not None else shared.lag if shared else None,
        "event_rate": None,
        "reconnect_count": None,
        "provider_errors": None,
        "ingestion_latency": None,
        "last_checkpoint_at": checkpoint.updated_at if checkpoint else None,
        "worker_status": shared.status if shared else worker.status if worker else "DOWN",
        "worker_last_heartbeat_at": shared.updated_at if shared else worker.last_heartbeat_at if worker else None,
        "worker_heartbeat_age_seconds": heartbeat_age,
        "shared_state": True,
    }
    source = "bsc_websocket" if settings.bsc_ws_endpoints else "bsc_http"
    if payload.get("status") in {"running", "RUNNING"}:
        source_health_registry.record_success(source, details={"stream": "bsc_logs"})
    else:
        source_health_registry.set_status(source, "UNKNOWN", details={"stream": "bsc_logs"})
    payload["metrics"] = observability.metrics.snapshot()
    details = (worker.details_json or {}) if worker else {}
    payload["active_provider"] = details.get("active_provider", settings.bsc_provider_name)
    payload["active_transport"] = details.get("active_transport", "ws" if settings.bsc_ws_endpoints else "http")
    payload["configured_provider_count"] = len(settings.bsc_ws_endpoints) + len(
        settings.bsc_rpc_endpoints
    )
    payload["failover_count"] = details.get("failover_count", 0)
    payload["failback_count"] = details.get("failback_count", 0)
    payload["runtime_worker_status"] = {
        "worker_name": shared.worker_name,
        "instance_id": shared.instance_id,
        "status": shared.status,
        "transport": shared.transport,
        "provider": shared.provider,
        "last_block": shared.last_block,
        "head_block": shared.head_block,
        "checkpoint_block": shared.checkpoint_block,
        "last_raw_log_at": shared.last_raw_log_at,
        "last_trade_at": shared.last_trade_at,
        "last_market_snapshot_at": shared.last_market_snapshot_at,
        "last_radar_update_at": shared.last_radar_update_at,
        "last_event_published_at": shared.last_event_published_at,
        "last_event_forwarded_at": shared.last_event_forwarded_at,
        "lag": shared.lag,
        "block_lag": shared.block_lag,
        "last_processed_at": shared.last_processed_at,
        "ingestion_phase": shared.ingestion_phase,
        "live_ready": shared.live_ready,
        "last_chunk_metrics": shared.last_chunk_metrics,
        "last_error": shared.last_error,
        "updated_at": shared.updated_at,
    } if shared else None
    return payload

"""研究型应用的命令行入口。"""

import argparse
import asyncio
import json
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from smms.chains.bsc import BSCProvider
from smms.chains.bsc.websocket import (
    BSCFailoverSubscription,
    BSCHTTPPollingSubscription,
    BSCWebsocketSubscription,
)
from smms.chains.constants import BSC_CHAIN_ID, normalize_bsc_address, validate_bsc_chain
from smms.config import get_settings
from smms.db.database import session_scope
from smms.ingestion import DataUnavailable
from smms.ingestion.bsc_stream import BSCRealtimeIngestionService
from smms.ingestion.flap import FlapHistoricalIngestionService
from smms.launchpads.flap import FlapConfig


def _finalize_live_bsc_readiness(result: dict[str, object], reasons: list[str]) -> None:
    factory = result.get("factory")
    factory_ok = (
        isinstance(factory, list)
        and bool(factory)
        and all(isinstance(item, dict) and item.get("deployed") for item in factory)
    )
    migration = result.get("migration")
    migration_ok = isinstance(migration, dict) and bool(migration.get("ready"))
    result["fanout_ready"] = result.get("redis") == "OK"
    blocking = {
        "WRONG_CHAIN", "DATABASE_UNAVAILABLE", "DATABASE_MIGRATION_NOT_HEAD",
        "RPC_UNUSABLE", "FACTORY_NOT_CONFIGURED", "FACTORY_VERIFICATION_FAILED",
    }
    result["ingestion_ready"] = (
        result.get("chain_id") == BSC_CHAIN_ID
        and result.get("database") == "OK"
        and migration_ok
        and result.get("rpc") == "OK"
        and factory_ok
        and not any(reason in blocking for reason in reasons)
    )
    result["reasons"] = list(dict.fromkeys(reasons))
    result["reason"] = ";".join(result["reasons"]) if result["reasons"] else None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="smms", description="聪明钱 Meme 情报引擎")
    commands = parser.add_subparsers(dest="command", required=True)

    radar = commands.add_parser("radar-refresh", help="Recover canonical radar candidates")
    radar.add_argument("--as-of", type=datetime.fromisoformat)

    radar_ops = commands.add_parser("radar", help="Radar priority audit queue")
    radar_commands = radar_ops.add_subparsers(dest="radar_command", required=True)
    worker = radar_commands.add_parser("worker", help="claim and process durable audit jobs")
    worker.add_argument("--worker-id", default="radar-worker")
    worker.add_argument("--once", action="store_true", help="process one job and exit")
    radar_commands.add_parser("queue", help="show durable audit queue metrics")
    audit = radar_commands.add_parser("audit", help="schedule an immediate audit")
    audit.add_argument("token")
    audit.add_argument("--priority", type=int, default=100)
    audit.add_argument("--reason", default="manual audit")

    commands.add_parser("health", help="检查数据库、Redis 和外部数据源状态")
    diagnostics = commands.add_parser("diagnostics", help="只读运行诊断")
    diagnostics.add_argument("diagnostics_command", choices=("live-bsc",))
    diagnostics.add_argument("--smoke", action="store_true", help="run read-only BSC smoke checks")
    for name in ("init-db", "ingest", "scan", "paper"):
        commands.add_parser(name)
    backtest = commands.add_parser("backtest", help="运行只读研究回测")
    backtest.add_argument("--from", dest="from_time", type=datetime.fromisoformat)
    backtest.add_argument("--to", dest="to_time", type=datetime.fromisoformat)
    backtest.add_argument("--strategy", default="composite", choices=("composite", "smart-money", "market_only", "market-only", "early_buyer_only", "random"))
    backtest.add_argument("--threshold", type=Decimal, default=Decimal(50))
    backtest.add_argument("--capital", type=Decimal, default=Decimal(1000))
    backtest.add_argument("--input", type=Path, help="包含 rows 数组的 JSON 文件")
    backtest.add_argument("--dataset", help="已物化 historical dataset id 或 dataset_version")
    backtest.add_argument("--output", type=Path, help="输出 JSON 文件")
    backtest.add_argument("--csv", type=Path, help="输出 evaluations CSV 文件")

    dataset = commands.add_parser("dataset", help="构建可复现的历史 point-in-time 数据集")
    dataset_commands = dataset.add_subparsers(dest="dataset_command", required=True)
    build_dataset = dataset_commands.add_parser("build", help="从数据库构建 historical dataset")
    build_dataset.add_argument("--from", dest="from_time", required=True, type=datetime.fromisoformat)
    build_dataset.add_argument("--to", dest="to_time", required=True, type=datetime.fromisoformat)
    build_dataset.add_argument("--chain", default="bsc")
    build_dataset.add_argument("--output", type=Path)
    build_dataset.add_argument("--dataset-version", required=True)

    alpha = commands.add_parser(
        "alpha", help="Phase 9 point-in-time Meme Alpha research"
    )
    alpha_commands = alpha.add_subparsers(dest="alpha_command", required=True)
    alpha_backtest = alpha_commands.add_parser(
        "backtest", help="run the Alpha baseline on canonical historical facts"
    )
    alpha_backtest.add_argument(
        "--from", dest="from_time", required=True, type=datetime.fromisoformat
    )
    alpha_backtest.add_argument("--to", dest="to_time", required=True, type=datetime.fromisoformat)
    alpha_backtest.add_argument(
        "--observation-window", choices=("30s", "1m", "5m"), default="1m"
    )
    alpha_backtest.add_argument(
        "--future-horizon", choices=("15m", "1h", "6h", "24h", "3d", "7d"), default="1h"
    )
    alpha_backtest.add_argument(
        "--coverage-manifest", type=Path, help="verified historical coverage JSON"
    )
    alpha_backtest.add_argument("--output", type=Path, help="write the reproducible JSON report")
    alpha_backtest.add_argument(
        "--samples-output", type=Path, help="write point-in-time feature and label samples as JSON"
    )
    alpha_coverage = alpha_commands.add_parser(
        "coverage", help="build an auditable PancakeSwap V2 historical coverage manifest"
    )
    alpha_coverage_commands = alpha_coverage.add_subparsers(
        dest="coverage_command", required=True
    )
    alpha_coverage_build = alpha_coverage_commands.add_parser(
        "build", help="scan a complete block range and write coverage JSON"
    )
    alpha_coverage_build.add_argument("--from-block", dest="from_block", required=True, type=int)
    alpha_coverage_build.add_argument("--to-block", dest="to_block", required=True, type=int)
    alpha_coverage_build.add_argument("--chunk-size", type=int, default=5_000)
    alpha_coverage_build.add_argument("--output", type=Path, required=True)

    discover = commands.add_parser("discover", help="发现链上 Token")
    discover_commands = discover.add_subparsers(dest="launchpad", required=True)
    discover_flap = discover_commands.add_parser("flap", help="发现 Flap Token 创建事件")
    _add_block_range(discover_flap)
    discover_flap.add_argument(
        "--config", type=Path, default=Path("configs/sources.yaml"), help="Flap YAML 配置文件"
    )

    backfill = commands.add_parser("backfill", help="回填历史链上数据")
    backfill_commands = backfill.add_subparsers(dest="launchpad", required=True)
    backfill_flap = backfill_commands.add_parser("flap", help="回填单个 Flap Token")
    backfill_flap.add_argument("--token", required=True, help="目标 Token 合约地址")
    _add_block_range(backfill_flap)
    backfill_flap.add_argument(
        "--config", type=Path, default=Path("configs/sources.yaml"), help="Flap YAML 配置文件"
    )

    verify = commands.add_parser("verify", help="验证指定链上交易中的 Flap 事件")
    verify_commands = verify.add_subparsers(dest="launchpad", required=True)
    verify_flap = verify_commands.add_parser("flap", help="验证 Flap 交易回执和事件日志")
    verify_flap.add_argument("--tx", required=True, help="交易哈希")
    verify_flap.add_argument("--event", choices=("TokenCreated", "TokenBought", "TokenSold"))
    verify_flap.add_argument(
        "--config", type=Path, default=Path("configs/sources.yaml"), help="Flap YAML 配置文件"
    )

    quality = commands.add_parser("quality", help="输出 Flap 数据质量报告")
    quality_commands = quality.add_subparsers(dest="launchpad", required=True)
    quality_flap = quality_commands.add_parser("flap", help="回填并检查单个 Flap Token")
    quality_flap.add_argument("--token", required=True, help="目标 Token 合约地址")
    _add_block_range(quality_flap)
    quality_flap.add_argument(
        "--config", type=Path, default=Path("configs/sources.yaml"), help="Flap YAML 配置文件"
    )

    bsc = commands.add_parser("bsc", help="BSC 实时链路")
    bsc_commands = bsc.add_subparsers(dest="bsc_command", required=True)
    stream = bsc_commands.add_parser("stream", help="管理 BSC 实时事件流")
    stream_commands = stream.add_subparsers(dest="stream_command", required=True)
    stream_commands.add_parser("status", help="输出实时流状态")
    stream_commands.add_parser("checkpoint", help="输出实时流 checkpoint")
    stream_commands.add_parser("start", help="启动实时事件摄取")

    intelligence = commands.add_parser("intelligence", help="物化聪明钱情报快照")
    intelligence_commands = intelligence.add_subparsers(
        dest="intelligence_command", required=True
    )
    refresh = intelligence_commands.add_parser("refresh", help="刷新钱包与 Token 快照")
    refresh.add_argument("--chain", default="bsc")
    refresh.add_argument("--as-of", type=datetime.fromisoformat)
    refresh.add_argument("--score-version", default="v2")
    refresh.add_argument("--profile-version", default="v1")
    refresh.add_argument("--min-wallet-score", type=Decimal, default=Decimal(60))

    risk = commands.add_parser("risk", help="物化 Token 风险快照")
    risk_commands = risk.add_subparsers(dest="risk_command", required=True)
    risk_refresh = risk_commands.add_parser("refresh", help="刷新风险快照")
    risk_refresh.add_argument("--chain", default="bsc")
    risk_refresh.add_argument("--token")
    risk_refresh.add_argument("--as-of", type=datetime.fromisoformat)
    risk_refresh.add_argument("--risk-version", default="risk-v1")

    return parser


def _add_block_range(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--from-block", type=int)
    parser.add_argument("--to-block", type=int)


def _require_block_range(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if args.from_block is None or args.to_block is None:
        parser.error("历史扫描必须同时提供 --from-block 和 --to-block")


def _load_flap_config(path: Path) -> FlapConfig:
    config = FlapConfig.from_yaml(path)
    if not config.enabled:
        raise DataUnavailable("Flap 配置已禁用；请先完成链上验证")
    try:
        config.validate_runtime()
    except ValueError as exc:
        raise DataUnavailable(str(exc)) from exc
    return config


def _load_flap_verification_config(path: Path) -> FlapConfig:
    """Load selectors for read-only verification without enabling ingestion."""

    return FlapConfig.from_yaml(path)


async def run_discover_flap(
    *,
    provider: BSCProvider,
    config: FlapConfig,
    from_block: int,
    to_block: int,
) -> list[dict[str, object]]:
    service = FlapHistoricalIngestionService(provider, config)
    records = await service.discover_tokens(from_block=from_block, to_block=to_block)
    return [
        {
            "chain": record.chain,
            "launchpad": record.launchpad,
            "token_address": record.token_address,
            "creator_address": record.creator_address,
            "created_at": record.created_at.isoformat(),
            "creation_tx_hash": record.creation_tx_hash,
            "creation_block": record.creation_block,
        }
        for record in records
    ]


async def run_backfill_flap(
    *,
    provider: BSCProvider,
    config: FlapConfig,
    token_address: str,
    from_block: int,
    to_block: int,
) -> str:
    settings = get_settings()
    async with session_scope(settings) as session:
        result = await FlapHistoricalIngestionService(
            provider,
            config,
            session=session,
        ).backfill_token(
            token_address,
            from_block=from_block,
            to_block=to_block,
        )
    return result.quality.render()


async def run_verify_flap(
    *, provider: BSCProvider, config: FlapConfig, tx_hash: str, event: str | None
) -> list[dict[str, object]]:
    from smms.launchpads.flap.verification import verify_real_transaction

    if event:
        expected_emitter = (
            config.creation_contract_address
            if event == "TokenCreated"
            else config.trade_contract_address
        )
        if not expected_emitter:
            raise DataUnavailable(
                f"Flap emitter for {event} is not configured; provide verified contract evidence"
            )
    else:
        expected_emitter = None
        expected_emitters = {
            name: emitter
            for name, emitter in (
                ("TokenCreated", config.creation_contract_address),
                ("TokenBought", config.trade_contract_address),
                ("TokenSold", config.trade_contract_address),
            )
            if emitter
        }
        if len(expected_emitters) < 3:
            raise DataUnavailable(
                "Flap creation and trade emitters are required when --event is omitted"
            )
    if event:
        expected_emitters = None
    evidence = await verify_real_transaction(
        provider,
        tx_hash,
        event=event,
        expected_emitter=expected_emitter,
        expected_emitters=expected_emitters,
    )
    return [item.as_dict() for item in evidence]


def cli(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "radar-refresh":
        settings = get_settings()
        from smms.radar.engine import RadarEngine
        async def refresh_radar():
            async with session_scope(settings) as session:
                await session.run_sync(lambda sync: RadarEngine(sync, settings.radar,
                    chain_id=settings.bsc_chain_id).recover(at=args.as_of))
                await session.commit()
        asyncio.run(refresh_radar())
        print("Radar candidates refreshed")
        return
    if args.command == "radar":
        from sqlalchemy import select

        from smms.db.models import RadarCandidate, Token
        from smms.radar.audit import AuditQueue, AuditWorker

        async def radar_operation() -> object:
            settings = get_settings()
            async with session_scope(settings) as session:
                def operation(sync_session):
                    queue = AuditQueue(sync_session)
                    if args.radar_command == "queue":
                        return queue.metrics()
                    if args.radar_command == "audit":
                        token = sync_session.scalar(select(Token).where(Token.address == normalize_bsc_address(args.token), Token.chain == "bsc"))
                        if token is None:
                            raise ValueError(f"token not found: {args.token}")
                        candidate = sync_session.scalar(select(RadarCandidate).where(RadarCandidate.token_id == token.id))
                        if candidate is None:
                            raise ValueError(f"radar candidate not found: {args.token}")
                        job = queue.schedule(candidate, reason=args.reason, priority=args.priority)
                        return {"job_id": job.id, "status": job.status.value, "token": job.token_address}
                    worker = AuditWorker(sync_session, worker_id=args.worker_id)
                    job = worker.process_once()
                    return {"processed": job.id if job else None, **queue.metrics()}
                result = await session.run_sync(operation)
                await session.commit()
                return result

        try:
            print(json.dumps(asyncio.run(radar_operation()), sort_keys=True, default=str))
        except ValueError as exc:
            parser.error(str(exc))
        return
    if args.command == "health":
        print("请访问 API 的 GET /health 端点检查依赖状态。")
        return
    if args.command == "diagnostics" and args.diagnostics_command == "live-bsc":
        async def run_diagnostics() -> dict[str, object]:
            settings = get_settings()
            reasons: list[str] = []
            result: dict[str, object] = {
                "chain": "bsc", "chain_id": BSC_CHAIN_ID,
                "rpc": "NOT_CONFIGURED", "ws": "NOT_CONFIGURED",
                "redis": "UNKNOWN", "database": "UNKNOWN",
                "factory": "DEGRADED:FACTORY_NOT_CONFIGURED", "last_raw_log": None,
                "last_trade_event": None, "last_market_snapshot": None,
                "last_smart_money_update": None, "last_radar_update": None,
                "last_sse_publish": None,
                "ingestion_ready": False, "fanout_ready": False,
                "migration": {"current": None, "head": None, "ready": False},
                "reasons": reasons,
            }
            if settings.bsc_chain_id != BSC_CHAIN_ID:
                reasons.append("WRONG_CHAIN")
            factories = [
                item.strip()
                for item in (settings.bsc_dex_factory_addresses or "").split(",")
                if item.strip()
            ]
            if not factories:
                reasons.append("FACTORY_NOT_CONFIGURED")
            if settings.bsc_rpc_endpoints:
                provider = BSCProvider(settings, verify_on_use=True)
                try:
                    await provider.ensure_usable()
                    result["rpc"] = "OK"
                    result["chain_id"] = await provider.get_chain_id()
                    if result["chain_id"] != BSC_CHAIN_ID:
                        reasons.append("WRONG_CHAIN")
                    else:
                        result["rpc_capable"] = True
                    result["factory"] = (
                        [await provider.verify_factory(item) for item in factories]
                        if factories
                        else "DEGRADED:FACTORY_NOT_CONFIGURED"
                    )
                    if factories and not all(item.get("deployed", False) for item in result["factory"] if isinstance(item, dict)):
                        reasons.append("FACTORY_VERIFICATION_FAILED")
                    if args.smoke:
                        result["rpc_block_number"] = await provider.get_latest_block_number()
                        result["rpc_capabilities"] = await provider.probe_capabilities()
                except (DataUnavailable, OSError, RuntimeError, ValueError) as exc:
                    result["rpc"] = f"DEGRADED:{type(exc).__name__}"
                    reasons.append("RPC_UNUSABLE")
            else:
                reasons.append("RPC_NOT_CONFIGURED")
            if settings.bsc_ws_endpoints:
                result["ws"] = "CONFIGURED"
                if args.smoke:
                    ws_probe = BSCWebsocketSubscription(settings)
                    try:
                        smoke_result = await ws_probe.probe_smoke(
                            factory_address=factories[0] if factories else None
                        )
                        result.update({
                            key: value
                            for key, value in smoke_result.items()
                            if key not in {"reason", "ready"}
                        })
                        result["ws_smoke"] = bool(smoke_result.get("ready"))
                        result["ws_reason"] = smoke_result.get("reason")
                        if smoke_result.get("reason"):
                            reasons.append(str(smoke_result["reason"]))
                    except Exception as exc:  # noqa: BLE001 - diagnostics must report failure
                        result["latency_ms"] = None
                        result["ws_connect"] = f"FAILED:{type(exc).__name__}"
                        result["ws_chain_id"] = f"FAILED:{type(exc).__name__}"
                        result["ws_logs_subscription"] = f"FAILED:{type(exc).__name__}"
                        result["ws_unsubscribe"] = f"FAILED:{type(exc).__name__}"
                        reasons.append("WS_SUBSCRIPTION_UNUSABLE")
                else:
                    result["ws_smoke"] = False
            else:
                reasons.append("NO_INGESTION_TRANSPORT")
            from smms.db.redis import check_redis
            redis_state = await check_redis(settings)
            result["redis"] = "DEGRADED" if str(redis_state).lower() in {"unavailable", "degraded"} else "OK"
            result["fanout_ready"] = result["redis"] == "OK"
            try:
                async with session_scope(settings) as session:
                    def read(sync):
                        from alembic.config import Config
                        from alembic.script import ScriptDirectory
                        from sqlalchemy import desc, inspect, select, text

                        from smms.db.models import RawChainEvent, TokenMarketSnapshot, TradeEvent
                        payload = {
                            "last_raw_log": sync.execute(select(RawChainEvent).order_by(desc(RawChainEvent.ingestion_time)).limit(1)).scalar_one_or_none(),
                            "last_trade_event": sync.execute(select(TradeEvent).order_by(desc(TradeEvent.timestamp)).limit(1)).scalar_one_or_none(),
                            "last_market_snapshot": sync.execute(select(TokenMarketSnapshot).order_by(desc(TokenMarketSnapshot.as_of)).limit(1)).scalar_one_or_none(),
                        }
                        current = (
                            sync.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
                            if inspect(sync.get_bind()).has_table("alembic_version")
                            else None
                        )
                        script = ScriptDirectory.from_config(Config("alembic.ini"))
                        head = script.get_current_head()
                        payload["migration"] = {"current": current, "head": head, "ready": current == head}
                        return payload
                    rows = await session.run_sync(read)
                    result["database"] = "OK"
                    for key, row in rows.items():
                        if key == "migration":
                            result[key] = row
                            continue
                        result[key] = {"tx_hash": getattr(row, "tx_hash", None), "token": getattr(row, "token_address", None), "as_of": getattr(row, "as_of", None)} if row else None
                    migration = result.get("migration") or {}
                    if not migration.get("ready", False):
                        reasons.append("DATABASE_MIGRATION_NOT_HEAD")
            except (OSError, RuntimeError, ValueError, SQLAlchemyError):
                result["database"] = "DEGRADED"
                reasons.append("DATABASE_UNAVAILABLE")
            _finalize_live_bsc_readiness(result, reasons)
            return result
        print(json.dumps(asyncio.run(run_diagnostics()), ensure_ascii=False, default=str, sort_keys=True))
        return
    if args.command == "bsc":
        try:
            settings = get_settings()
            provider_name = settings.bsc_provider_name
            def make_stream():
                if settings.bsc_ws_endpoints:
                    websocket = BSCWebsocketSubscription(settings)
                    if settings.bsc_rpc_endpoints:
                        return BSCFailoverSubscription(
                            websocket,
                            BSCHTTPPollingSubscription(
                                BSCProvider(settings, verify_on_use=True), settings
                            ),
                        )
                    return websocket
                return BSCHTTPPollingSubscription(
                    BSCProvider(settings, verify_on_use=True), settings
                )
            if args.stream_command == "status":
                print(json.dumps({"provider": provider_name, "ws_configured": bool(settings.bsc_ws_endpoints), "rpc_configured": bool(settings.bsc_rpc_endpoints)}, sort_keys=True))
                return
            if args.stream_command == "checkpoint":
                async def read_checkpoint() -> dict[str, object]:
                    async with session_scope(settings) as session:
                        service = BSCRealtimeIngestionService(
                            make_stream(),
                            chain_id=settings.bsc_chain_id,
                            provider_name=provider_name,
                            session=session,
                        )
                        return await service.status()
                print(json.dumps(asyncio.run(read_checkpoint()), sort_keys=True, default=str))
                return
            if args.stream_command == "start":
                async def start_stream() -> None:
                    stream = make_stream()
                    async with session_scope(settings) as session:
                        await BSCRealtimeIngestionService(
                            stream,
                            chain_id=settings.bsc_chain_id,
                            provider_name=provider_name,
                            session=session,
                            chain_provider=BSCProvider(settings, verify_on_use=True),
                            confirmation_depth=settings.bsc_confirmation_depth,
                        ).run()
                asyncio.run(start_stream())
                return
        except (DataUnavailable, OSError, ValueError) as exc:
            parser.error(str(exc))
    if args.command == "intelligence":
        from smms.smart_money.aggregation import IntelligenceAggregationService

        async def refresh_intelligence() -> object:
            settings = get_settings()
            async with session_scope(settings) as session:
                result = await session.run_sync(
                    lambda sync_session: IntelligenceAggregationService(
                        sync_session,
                        chain=validate_bsc_chain(args.chain),
                        score_version=args.score_version,
                        profile_version=args.profile_version,
                        min_wallet_score=args.min_wallet_score,
                    ).refresh(as_of=args.as_of)
                )
                await session.commit()
                return result

        result = asyncio.run(refresh_intelligence())
        print(json.dumps(result.__dict__, sort_keys=True, default=str))
        return
    if args.command == "risk":
        from smms.risk.aggregation import RiskAggregationService

        async def refresh_risk() -> int:
            settings = get_settings()
            async with session_scope(settings) as session:
                count = await session.run_sync(
                    lambda sync_session: RiskAggregationService(
                        sync_session, chain=validate_bsc_chain(args.chain), risk_version=args.risk_version
                    ).refresh(as_of=args.as_of, token_address=args.token)
                )
                await session.commit()
                return count

        print(json.dumps({"risk_snapshots": asyncio.run(refresh_risk())}, sort_keys=True))
        return
    if args.command == "backtest":
        from smms.backtest.signal_engine import run_backtest
        rows = []
        if args.dataset:
            from sqlalchemy import select

            from smms.db.models import BacktestDataset, BacktestDatasetRow
            async def load_dataset() -> list[dict[str, object]]:
                settings = get_settings()
                async with session_scope(settings) as session:
                    query = select(BacktestDataset)
                    query = query.where(BacktestDataset.id == int(args.dataset)) if str(args.dataset).isdigit() else query.where(BacktestDataset.dataset_version == args.dataset)
                    dataset_row = await session.scalar(query)
                    if dataset_row is None:
                        raise DataUnavailable(f"dataset not found: {args.dataset}")
                    records = (await session.scalars(select(BacktestDatasetRow).where(BacktestDatasetRow.dataset_id == dataset_row.id).order_by(BacktestDatasetRow.signal_timestamp))).all()
                    return [record.payload_json for record in records]
            rows = asyncio.run(load_dataset())
        if not args.dataset and args.input and args.input.exists():
            payload = json.loads(args.input.read_text(encoding="utf-8"))
            rows = payload.get("rows", payload) if isinstance(payload, (dict, list)) else []
        if args.from_time or args.to_time:
            def in_range(row: dict) -> bool:
                stamp = datetime.fromisoformat(str(row["timestamp"]))
                return (args.from_time is None or stamp >= args.from_time) and (args.to_time is None or stamp <= args.to_time)
            rows = [row for row in rows if in_range(row)]
        result = run_backtest(rows, strategy=args.strategy, threshold=args.threshold, capital=args.capital)
        payload = result.as_dict()
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        if args.output:
            args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        if args.csv:
            import csv
            with args.csv.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=("horizon", "return_pct", "high_return", "low_return", "available", "rug"))
                writer.writeheader()
                writer.writerows({key: item.get(key) for key in writer.fieldnames} for item in payload["evaluations"])
        return
    if args.command == "alpha":
        from smms.alpha import AlphaBacktest, AlphaDatasetBuilder, ResearchCoverageManifest

        if args.alpha_command == "coverage":
            from smms.alpha.coverage import HistoricalCoverageConfig, run_coverage_scan

            settings = get_settings()
            manifest = run_coverage_scan(
                settings,
                HistoricalCoverageConfig(
                    start_block=args.from_block,
                    end_block=args.to_block,
                    chunk_size=args.chunk_size,
                    output=args.output,
                ),
            )
            print(json.dumps(manifest.as_dict(), ensure_ascii=False, indent=2, default=str))
            return

        async def run_alpha_backtest() -> tuple[dict[str, object], list[dict[str, object]]]:
            settings = get_settings()
            async with session_scope(settings) as session:
                def build(sync_session):
                    manifest = (
                        ResearchCoverageManifest.read_json(
                            args.coverage_manifest,
                            start_block=None,
                            end_block=None,
                        )
                        if args.coverage_manifest
                        else None
                    )
                    samples = AlphaDatasetBuilder(
                        sync_session, coverage_manifest=manifest
                    ).build(args.from_time, args.to_time, windows=(args.observation_window,))
                    report = AlphaBacktest(samples).report(
                        observation_window=args.observation_window, horizon=args.future_horizon
                    )
                    return report, [sample.as_dict() for sample in samples]

                return await session.run_sync(build)

        payload, samples = asyncio.run(run_alpha_backtest())
        rendered = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        print(rendered)
        if args.output:
            args.output.write_text(rendered, encoding="utf-8")
        if args.samples_output:
            args.samples_output.write_text(
                json.dumps(samples, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
            )
        return
    if args.command == "dataset":
        from smms.backtest.dataset import HistoricalDatasetBuilder
        async def build_dataset() -> dict[str, object]:
            settings = get_settings()
            async with session_scope(settings) as session:
                def sync_build(sync_session):
                    builder = HistoricalDatasetBuilder(sync_session, chain=validate_bsc_chain(args.chain), chain_id=BSC_CHAIN_ID)
                    dataset_row, report = builder.materialize(args.from_time, args.to_time, dataset_version=args.dataset_version)
                    return {"dataset_id": dataset_row.id, "dataset_version": dataset_row.dataset_version, "config_hash": dataset_row.config_hash, "from": dataset_row.period_from, "to": dataset_row.period_to, "report": report.as_dict()}
                return await session.run_sync(sync_build)
        payload = asyncio.run(build_dataset())
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        if args.output:
            args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return
    if args.command in {"init-db", "ingest", "scan", "paper"}:
        print(f"命令 {args.command} 预留给后续阶段。")
        return

    try:
        if args.command in {"discover", "backfill", "quality"}:
            _require_block_range(parser, args)
            config = _load_flap_config(args.config)
        elif args.command == "verify":
            config = _load_flap_verification_config(args.config)
        else:
            parser.error("unsupported command")
        settings = get_settings()
        provider = BSCProvider(settings, verify_on_use=True)
        asyncio.run(provider.ensure_usable())
        if args.command == "discover":
            records = asyncio.run(
                run_discover_flap(
                    provider=provider,
                    config=config,
                    from_block=args.from_block,
                    to_block=args.to_block,
                )
            )
            for record in records:
                print(json.dumps(record, sort_keys=True))
            return
        if args.command == "backfill":
            print(
                asyncio.run(
                    run_backfill_flap(
                        provider=provider,
                        config=config,
                        token_address=args.token,
                        from_block=args.from_block,
                        to_block=args.to_block,
                    )
                )
            )
            return
        if args.command == "verify":
            records = asyncio.run(
                run_verify_flap(
                    provider=provider,
                    config=config,
                    tx_hash=args.tx,
                    event=args.event,
                )
            )
            for record in records:
                print(json.dumps(record, sort_keys=True, default=str))
            return
        if args.command == "quality":
            print(
                asyncio.run(
                    run_backfill_flap(
                        provider=provider,
                        config=config,
                        token_address=args.token,
                        from_block=args.from_block,
                        to_block=args.to_block,
                    )
                )
            )
            return
    except (DataUnavailable, OSError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    cli()

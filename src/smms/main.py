"""研究型应用的命令行入口。"""

import argparse
import asyncio
import json
from collections.abc import Sequence
from pathlib import Path

from smms.chains.bsc import BSCProvider
from smms.config import get_settings
from smms.db.database import session_scope
from smms.ingestion import DataUnavailable
from smms.ingestion.flap import FlapHistoricalIngestionService
from smms.launchpads.flap import FlapConfig


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="smms", description="聪明钱 Meme 情报引擎")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("health", help="检查数据库、Redis 和外部数据源状态")
    for name in ("init-db", "ingest", "scan", "backtest", "paper"):
        commands.add_parser(name)

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
    if args.command == "health":
        print("请访问 API 的 GET /health 端点检查依赖状态。")
        return
    if args.command in {"init-db", "ingest", "scan", "backtest", "paper"}:
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
        provider = BSCProvider(settings)
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

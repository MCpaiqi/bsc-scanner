"""Long-running production worker owners for the Compose topology."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import socket
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from smms.config.settings import get_settings
from smms.db.database import session_scope
from smms.db.models import RuntimeWorkerStatus, StreamCheckpoint, WorkerHeartbeat
from smms.ingestion import DataUnavailable
from smms.observability import source_health_registry
from smms.radar.audit import AuditWorker
from smms.radar.live_forward import evaluate_due


def _instance_id() -> str:
    return os.getenv("SMMS_INSTANCE_ID", f"{socket.gethostname()}-{os.getpid()}")


def bsc_service_failure_details(exc: Exception, *, transport: str | None) -> dict[str, object]:
    """Make a failed ingestion cycle explicitly degraded before it retries."""
    return {
        "reason": "SERVICE_CYCLE_FAILED",
        "last_error": f"{type(exc).__name__}: {exc!s}",
        "active_transport": transport,
        "ingestion_ready": False,
    }


async def heartbeat(worker_type: str, status: str, current_job: str | None = None, details: dict | None = None) -> None:
    now = datetime.now(UTC)
    async with session_scope(get_settings()) as session:
        def write(sync):
            row = sync.scalar(
                select(WorkerHeartbeat).where(
                    WorkerHeartbeat.instance_id == _instance_id(),
                    WorkerHeartbeat.worker_type == worker_type,
                )
            )
            if row is None:
                row = WorkerHeartbeat(
                    instance_id=_instance_id(), worker_type=worker_type,
                    last_heartbeat_at=now, status=status, current_job=current_job,
                    details_json={},
                )
                sync.add(row)
            else:
                row.last_heartbeat_at, row.status, row.current_job = now, status, current_job
                row.details_json = {**(row.details_json or {}), **(details or {})}
            sync.flush()
            shared = sync.scalar(
                select(RuntimeWorkerStatus).where(RuntimeWorkerStatus.worker_name == worker_type)
            )
            if shared is None:
                sync.add(
                    RuntimeWorkerStatus(
                        worker_name=worker_type,
                        instance_id=_instance_id(),
                        status=status,
                        transport=(details or {}).get("active_transport"),
                        provider=(details or {}).get("active_provider"),
                        updated_at=now,
                    )
                )
            else:
                shared.instance_id = _instance_id()
                # The ingestion service writes CATCHING_UP/LIVE atomically
                # with its checkpoint. A process heartbeat proves liveness,
                # but must not erase that authoritative edge state.
                shared.status = (
                    shared.ingestion_phase
                    if worker_type in {"bsc-ingestion", "bsc-backfill"}
                    and status == "RUNNING"
                    and shared.ingestion_phase in {"CATCHING_UP", "LIVE", "DEGRADED", "FAILED"}
                    else status
                )
                shared.transport = (details or {}).get("active_transport", shared.transport)
                shared.provider = (details or {}).get("active_provider", shared.provider)
                shared.updated_at = now
                if worker_type == "bsc-live-tail" and details and details.get("head_block") is not None:
                    head_block = int(details["head_block"])
                    checkpoint_block = int(details.get("checkpoint_block") or 0)
                    previous_head = shared.head_block
                    if previous_head is None or head_block > int(previous_head):
                        shared.head_last_advanced_at = now
                    shared.head_block = head_block
                    shared.last_block = head_block
                    shared.checkpoint_block = checkpoint_block
                    shared.block_lag = max(0, head_block - checkpoint_block)
                    shared.lag = shared.block_lag
                    phase = str(details.get("ingestion_phase") or "CATCHING_UP")
                    shared.ingestion_phase = phase
                    shared.status = phase
                    shared.live_ready = phase == "LIVE"
                    if phase != "DEGRADED":
                        shared.last_error = None
                # A quiet HTTP live-tail must fail closed if its provider
                # head has stopped advancing.  This runs independently of
                # log arrival, so a zero-lag old head can never stay LIVE.
                if worker_type == "bsc-live-tail" and shared.head_last_advanced_at is not None:
                    advanced_at = shared.head_last_advanced_at
                    if advanced_at.tzinfo is None:
                        advanced_at = advanced_at.replace(tzinfo=UTC)
                    else:
                        advanced_at = advanced_at.astimezone(UTC)
                    stale_after = float(get_settings().bsc_head_stale_after_seconds)
                    staleness = max(0.0, (now - advanced_at).total_seconds())
                    shared.head_staleness_seconds = round(staleness, 3)
                    if staleness > stale_after:
                        shared.status = "DEGRADED"
                        shared.ingestion_phase = "DEGRADED"
                        shared.live_ready = False
                        shared.last_error = "RPC_HEAD_STALE"
                if details:
                    for key in ("last_block", "checkpoint_block", "lag"):
                        if details.get(key) is not None:
                            setattr(shared, key, details[key])
                    if details.get("last_error") is not None:
                        shared.last_error = str(details["last_error"])[:500]
                    for key in (
                        "last_raw_log_at", "last_trade_at", "last_market_snapshot_at",
                        "last_radar_update_at", "last_event_published_at", "last_event_forwarded_at",
                    ):
                        if details.get(key) is not None:
                            setattr(shared, key, details[key])
            sync.flush()
        await session.run_sync(write)
        await session.commit()


async def market_enrichment_heartbeat(*, count: int) -> None:
    """Report secondary enrichment without mutating canonical ingestion state."""
    await heartbeat(
        "bsc-market-enrichment", "RUNNING",
        details={"last_market_snapshot_at": datetime.now(UTC), "market_snapshots": count},
    )


async def run_bsc_metadata_backfill(settings) -> None:
    """Recover token names independently of swaps and DexScreener availability."""
    if not settings.bsc_rpc_endpoints:
        return
    from smms.chains.bsc import BSCProvider
    from smms.market.metadata import backfill_missing_erc20_metadata

    provider = BSCProvider(settings, verify_on_use=True)
    while True:
        try:
            async with session_scope(settings) as session:
                await backfill_missing_erc20_metadata(session, provider)
                await session.commit()
        except (DataUnavailable, OSError, RuntimeError, ValueError, SQLAlchemyError) as exc:
            source_health_registry.record_failure("bsc-token-metadata", exc)
        await asyncio.sleep(settings.market_snapshot_interval_seconds)


async def database_migration_at_head(settings) -> bool:
    """Require a reachable database whose Alembic revision is current."""
    try:
        async with session_scope(settings) as session:
            def check(sync) -> bool:
                from alembic.config import Config
                from alembic.script import ScriptDirectory
                from sqlalchemy import inspect, text

                if not inspect(sync.get_bind()).has_table("alembic_version"):
                    return False
                current = sync.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
                head = ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
                return current == head

            return bool(await session.run_sync(check))
    except (OSError, RuntimeError, ValueError, SQLAlchemyError):
        return False


def materialize_dexscreener_enrichment(sync, market_tokens: list[dict], *, settings) -> int:
    """Enrich only chain-discovered BSC pairs and refresh their Radar projection."""
    from smms.db.models import Pair, Token
    from smms.market.identity import display_text
    from smms.market.snapshots import materialize_dexscreener_snapshot
    from smms.radar.engine import RadarEngine

    count = 0
    for item in market_tokens:
        token_address = str(item.get("token_address") or "").lower()
        pair_address = str(item.get("pair_address") or "").lower()
        if not token_address or not pair_address:
            continue
        token = sync.scalar(
            select(Token).where(Token.chain == "bsc", Token.address == token_address)
        )
        pair = (
            sync.scalar(
                select(Pair).where(
                    Pair.chain_id == settings.bsc_chain_id,
                    Pair.address == pair_address,
                    Pair.canonical.is_(True),
                    Pair.removed.is_(False),
                    (Pair.token0_address == token_address) | (Pair.token1_address == token_address),
                )
            )
            if token is not None
            else None
        )
        if pair is None:
            continue
        # A secondary provider may fill missing identity only after the token/pair
        # has been discovered on chain. Keep existing ERC-20 metadata authoritative.
        identity_updated = False
        for field, max_length in (("symbol", 32), ("name", 128)):
            value = display_text(item.get(field), token_address)
            if display_text(getattr(token, field), token_address) is None and value:
                setattr(token, field, value[:max_length])
                identity_updated = True
        if identity_updated:
            token.metadata_json = {
                **(token.metadata_json or {}),
                "presentation_metadata_source": "DexScreener API",
            }
        snapshot = materialize_dexscreener_snapshot(sync, item)
        RadarEngine(sync, settings.radar, chain_id=settings.bsc_chain_id).refresh_for_event(
            pair, at=snapshot.as_of
        )
        count += 1
    return count


def load_target_pair_addresses(sync, *, settings) -> list[str]:
    """Select bounded, chain-discovered pairs for secondary market enrichment."""
    from smms.db.models import Pair

    rows = sync.scalars(
        select(Pair)
        .where(
            Pair.chain_id == settings.bsc_chain_id,
            Pair.canonical.is_(True),
            Pair.removed.is_(False),
        )
        .order_by(Pair.creation_timestamp.desc(), Pair.id.desc())
        .limit(getattr(settings, "bsc_max_active_pairs", 500))
    ).all()
    return [str(row.address) for row in rows]


async def run_bsc_role(
    *,
    worker_name: str,
    stream_name: str,
    start_from_head: bool,
    enable_market_enrichment: bool,
) -> None:
    settings = get_settings()
    worker_running = False
    head_provider = None
    live_edge_stable_observations = 0
    if not settings.bsc_rpc_endpoints and not settings.bsc_ws_endpoints:
        source_health_registry.set_status("bsc_websocket", "DEGRADED", details={"reason": "NOT_CONFIGURED"})
    async def heartbeat_loop() -> None:
        nonlocal head_provider, live_edge_stable_observations
        while True:
            configured = bool(settings.bsc_dex_factory_addresses and str(settings.bsc_dex_factory_addresses).strip())
            checkpoint_block = None
            head_block = None
            if worker_running:
                try:
                    async with session_scope(settings) as hb_session:
                        checkpoint_block = await hb_session.run_sync(
                            lambda sync: sync.scalar(
                                select(StreamCheckpoint.last_processed_block).where(
                                    StreamCheckpoint.chain_id == settings.bsc_chain_id,
                                    StreamCheckpoint.stream_name == stream_name,
                                )
                            )
                        )
                except (OSError, RuntimeError, ValueError, SQLAlchemyError):
                    checkpoint_block = None
            if worker_name == "bsc-live-tail" and checkpoint_block is not None:
                try:
                    from smms.chains.bsc import BSCProvider
                    from smms.ingestion.bsc_stream import ingestion_phase

                    head_provider = head_provider or BSCProvider(settings, verify_on_use=True)
                    head_block = await head_provider.get_latest_block_number()
                    lag = max(0, int(head_block) - int(checkpoint_block))
                    live_edge_stable_observations = (
                        live_edge_stable_observations + 1
                        if lag <= settings.bsc_live_lag_threshold
                        else 0
                    )
                    phase, _ = ingestion_phase(
                        head_block=head_block,
                        checkpoint_block=checkpoint_block,
                        stable_chunks=live_edge_stable_observations,
                        lag_threshold=settings.bsc_live_lag_threshold,
                        required_stable_chunks=settings.bsc_live_stable_chunks,
                    )
                except (DataUnavailable, OSError, RuntimeError, ValueError):
                    head_block = None
                    phase = "DEGRADED"
            else:
                phase = None
            await heartbeat(
                worker_name,
                "RUNNING" if worker_running else "DEGRADED",
                details={
                    "chain_id": settings.bsc_chain_id,
                    "reason": None if worker_running and configured else (None if configured else "FACTORY_NOT_CONFIGURED"),
                    "checkpoint_block": checkpoint_block,
                    "head_block": head_block,
                    "ingestion_phase": phase,
                },
            )
            await asyncio.sleep(settings.runtime_heartbeat_interval_seconds)

    async def enrichment_loop() -> None:
        if not enable_market_enrichment or not settings.dexscreener_enabled:
            return
        from smms.ingestion.market.dexscreener import DexScreenerMarketProvider

        provider = DexScreenerMarketProvider(
            base_url=settings.dexscreener_base_url,
            timeout=settings.dexscreener_timeout_seconds,
            rate_limit_per_second=settings.dexscreener_rate_limit_per_second,
            cache_ttl_seconds=settings.dexscreener_cache_ttl_seconds,
            api_key=settings.dexscreener_api_key.get_secret_value()
            if settings.dexscreener_api_key
            else None,
        )
        while True:
            try:
                async with session_scope(settings) as session:
                    pair_addresses = await session.run_sync(
                        lambda sync: load_target_pair_addresses(sync, settings=settings)
                    )
                payload = await provider.fetch_pairs(pair_addresses)
                tokens = payload.get("tokens", []) if isinstance(payload, dict) else []
                if tokens:
                    async with session_scope(settings) as session:
                        count = await session.run_sync(
                            lambda sync, market_tokens=tokens: materialize_dexscreener_enrichment(
                                sync, market_tokens, settings=settings
                            )
                        )
                        await session.commit()
                    await market_enrichment_heartbeat(count=count)
            except (DataUnavailable, OSError, RuntimeError, ValueError) as exc:
                source_health_registry.record_failure("dexscreener", exc)
            await asyncio.sleep(settings.market_snapshot_interval_seconds)

    heartbeat_task = asyncio.create_task(heartbeat_loop())
    enrichment_task = asyncio.create_task(enrichment_loop())
    background_tasks = [heartbeat_task, enrichment_task]
    if enable_market_enrichment:
        background_tasks.append(asyncio.create_task(run_bsc_metadata_backfill(settings)))
    try:
        while True:
            if not settings.bsc_rpc_endpoints:
                await heartbeat(worker_name, "DEGRADED", details={"reason": "RPC_UNUSABLE"})
                await asyncio.sleep(settings.runtime_heartbeat_interval_seconds)
                continue
            if not settings.bsc_dex_factory_addresses or not str(settings.bsc_dex_factory_addresses).strip():
                source_health_registry.set_status(
                    "bsc_websocket" if settings.bsc_ws_endpoints else "bsc_http",
                    "DEGRADED",
                    details={"reason": "FACTORY_NOT_CONFIGURED"},
                )
                await heartbeat(
                    worker_name,
                    "DEGRADED",
                    details={"reason": "FACTORY_NOT_CONFIGURED", "ingestion_ready": False},
                )
                await asyncio.sleep(max(settings.runtime_heartbeat_interval_seconds, 1.0))
                continue
            from smms.chains.bsc import BSCProvider
            from smms.chains.bsc.websocket import (
                BSCFailoverSubscription,
                BSCHTTPPollingSubscription,
                BSCWebsocketSubscription,
            )
            from smms.ingestion.bsc_stream import BSCRealtimeIngestionService

            http_provider = None
            if settings.bsc_rpc_endpoints:
                http_provider = BSCProvider(settings, verify_on_use=True)
                try:
                    await http_provider.ensure_usable()
                    if await http_provider.get_chain_id() != 56:
                        raise DataUnavailable("BSC RPC chain id mismatch")
                    factories = [
                        item.strip() for item in str(settings.bsc_dex_factory_addresses).split(",")
                        if item.strip()
                    ]
                    verified = [await http_provider.verify_factory(item) for item in factories]
                    if not verified or not all(item.get("deployed") for item in verified):
                        raise DataUnavailable("BSC factory verification failed")
                    if not await database_migration_at_head(settings):
                        raise DataUnavailable("database migration is not at head")
                except (DataUnavailable, OSError, RuntimeError) as exc:
                    source_health_registry.set_status(
                        "bsc_http", "DEGRADED", details={"reason": type(exc).__name__}
                    )
                    await heartbeat(worker_name, "DEGRADED")
                    await asyncio.sleep(settings.runtime_heartbeat_interval_seconds)
                    continue
            stream = BSCWebsocketSubscription(settings) if settings.bsc_ws_endpoints else BSCHTTPPollingSubscription(http_provider, settings)
            if settings.bsc_ws_endpoints and settings.bsc_rpc_endpoints:
                stream = BSCFailoverSubscription(stream, BSCHTTPPollingSubscription(http_provider, settings))
            try:
                async with session_scope(settings) as session:
                    service = BSCRealtimeIngestionService(
                        stream, chain_id=settings.bsc_chain_id, provider_name=settings.bsc_provider_name,
                        session=session, chain_provider=http_provider,
                        confirmation_depth=settings.bsc_confirmation_depth,
                        stream_name=stream_name,
                        worker_name=worker_name,
                        start_from_head=start_from_head,
                    )
                    service_task = asyncio.create_task(service.run())
                    # Catch-up is active work even before the live transport is
                    # ready; expose that state instead of reporting a healthy
                    # process as degraded while durable progress is advancing.
                    worker_running = True
                    await heartbeat(
                        worker_name, "RUNNING",
                        details={
                            "active_transport": getattr(stream, "active_transport", "http"),
                            "active_provider": getattr(stream, "provider", settings.bsc_provider_name),
                            "chain_id": settings.bsc_chain_id,
                            "health_state": "CATCHING_UP",
                        },
                    )
                    ready_task = asyncio.create_task(service.ready.wait())
                    try:
                        done, _ = await asyncio.wait(
                            {service_task, ready_task}, return_when=asyncio.FIRST_COMPLETED
                        )
                        if service_task in done:
                            await service_task
                            continue
                        await heartbeat(
                            worker_name, "RUNNING",
                            details={
                                "active_transport": getattr(stream, "active_transport", "http"),
                                "active_provider": getattr(stream, "provider", settings.bsc_provider_name),
                                "chain_id": settings.bsc_chain_id,
                                "health_state": "IDLE_HEALTHY",
                            },
                        )
                        await service_task
                    finally:
                        worker_running = False
                        if not ready_task.done():
                            ready_task.cancel()
            except (DataUnavailable, OSError, RuntimeError, SQLAlchemyError) as exc:
                transport = getattr(stream, "active_transport", None)
                source_health_registry.set_status(
                    "bsc_http" if transport == "http" else "bsc_websocket",
                    "DEGRADED",
                    details={"reason": type(exc).__name__},
                )
                await heartbeat(
                    worker_name,
                    "DEGRADED",
                    details=bsc_service_failure_details(exc, transport=transport),
                )
                await asyncio.sleep(settings.runtime_heartbeat_interval_seconds)
    finally:
        for task in background_tasks:
            task.cancel()
        await asyncio.gather(*background_tasks, return_exceptions=True)


async def run_bsc() -> None:
    """Own the continuous, gapless historical checkpoint recovery."""
    await run_bsc_role(
        worker_name="bsc-backfill",
        stream_name="bsc_realtime",
        start_from_head=False,
        enable_market_enrichment=True,
    )


async def run_bsc_live_tail() -> None:
    """Own the live edge without waiting for a distant backfill checkpoint."""
    await run_bsc_role(
        # Keep the live epoch's durable status independent from the historical
        # backfill lane.  Both lanes ingest BSC, but they have different
        # checkpoints and readiness contracts.
        worker_name="bsc-live-tail",
        stream_name=get_settings().bsc_live_tail_stream_name,
        start_from_head=True,
        enable_market_enrichment=False,
    )


async def run_radar() -> None:
    settings = get_settings()
    while True:
        await heartbeat("radar-worker", "RUNNING")
        async with session_scope(settings) as session:
            def work(sync):
                return AuditWorker(sync, worker_id=_instance_id()).process_once()
            await session.run_sync(work)
            await session.commit()
        await asyncio.sleep(1)


async def run_forward() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    settings = get_settings()
    while True:
        await heartbeat("forward-worker", "RUNNING")
        async with session_scope(settings) as session:
            await session.run_sync(lambda sync: evaluate_due(sync))
            from smms.alpha.live import process_live_alpha
            await session.run_sync(
                lambda sync: process_live_alpha(
                    sync,
                    now=datetime.now(UTC),
                    minimum_quote=Decimal(str(settings.alpha_min_future_quote_notional)),
                )
            )
            await session.commit()
        await asyncio.sleep(30)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("worker", choices=("bsc-ingestion", "bsc-live-tail", "radar-worker", "forward-worker"))
    worker = parser.parse_args().worker
    asyncio.run({
        "bsc-ingestion": run_bsc,
        "bsc-live-tail": run_bsc_live_tail,
        "radar-worker": run_radar,
        "forward-worker": run_forward,
    }[worker]())


if __name__ == "__main__":
    main()

"""Durable BSC realtime ingestion orchestration."""

import asyncio
import json
import logging
import os
import socket
import time
from collections.abc import Sequence
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from smms.chains.base import ChainLog, ChainProvider, LogStream
from smms.chains.bsc.active_pairs import ActivePairRegistry
from smms.chains.bsc.transactions import TransactionContextResolver
from smms.chains.bsc.websocket import BlockWindowDedupe, StreamMetrics
from smms.chains.constants import BSC_CHAIN_ID, normalize_bsc_address
from smms.db.models import MarketEvent, RawChainEvent, RuntimeWorkerStatus, TradeEvent
from smms.db.repositories import AsyncRawChainEventRepository, AsyncStreamCheckpointRepository
from smms.observability import metrics, source_health_registry

logger = logging.getLogger(__name__)


def pair_event_topic_filter(config: Any) -> tuple[tuple[str, ...],]:
    """Match any supported PancakeSwap V2 event in topic0.

    JSON-RPC topic filters are positional. The alternatives must be nested in
    their single topic0 slot; a flat four-item sequence means an impossible
    four-topic AND filter and returns no Swap logs.
    """
    return ((
        config.swap_topic,
        config.sync_topic,
        config.mint_topic,
        config.burn_topic,
    ),)


def ingestion_phase(
    *,
    head_block: int,
    checkpoint_block: int,
    stable_chunks: int,
    lag_threshold: int,
    required_stable_chunks: int,
    head_stale: bool = False,
) -> tuple[str, bool]:
    """Return live status from durable progress, never process liveness alone."""
    if head_stale:
        return "DEGRADED", False
    if max(0, int(head_block) - int(checkpoint_block)) > int(lag_threshold):
        return "CATCHING_UP", False
    if int(stable_chunks) < int(required_stable_chunks):
        return "CATCHING_UP", False
    return "LIVE", True


def rpc_method_summary(requests: Sequence[dict[str, Any]]) -> dict[str, dict[str, int | float]]:
    """Aggregate credential-free RPC timings by JSON-RPC method."""
    counts: dict[str, int] = {}
    latency_ms: dict[str, float] = {}
    for request in requests:
        method = str(request.get("method") or "UNKNOWN")
        counts[method] = counts.get(method, 0) + 1
        latency_ms[method] = round(
            latency_ms.get(method, 0.0) + float(request.get("latency_ms") or 0), 2
        )
    return {"counts": counts, "latency_ms": latency_ms}


class BSCRealtimeIngestionService:
    """Persist and checkpoint a normalized BSC log stream with replay semantics."""

    stream_name = "bsc_realtime"
    checkpoint_provider = "bsc"

    def __init__(
        self,
        stream: LogStream,
        *,
        chain_id: int = BSC_CHAIN_ID,
        provider_name: str = "bsc_websocket",
        session: AsyncSession | None = None,
        chain_provider: ChainProvider | None = None,
        confirmation_depth: int = 3,
        active_pair_registry: ActivePairRegistry | None = None,
        transaction_resolver: TransactionContextResolver | None = None,
        defer_radar_projection: bool = False,
        stream_name: str | None = None,
        worker_name: str = "bsc-ingestion",
        start_from_head: bool = False,
    ) -> None:
        self.stream = stream
        if chain_id != BSC_CHAIN_ID:
            raise ValueError(f"BSC ingestion requires chain id {BSC_CHAIN_ID}")
        self.chain_id = BSC_CHAIN_ID
        self.stream_name = stream_name or type(self).stream_name
        self.worker_name = worker_name
        self.start_from_head = start_from_head
        self.provider_name = provider_name
        self.session = session
        self.chain_provider = chain_provider
        self.confirmation_depth = confirmation_depth
        stream_settings = getattr(stream, "settings", None)
        self.active_pair_registry = active_pair_registry or ActivePairRegistry(
            max_active_pairs=getattr(stream_settings, "bsc_max_active_pairs", 500),
            activity_ttl_seconds=getattr(stream_settings, "bsc_pair_activity_ttl_seconds", 3600),
            subscription_batch_size=getattr(stream_settings, "bsc_pair_subscription_batch_size", 50),
        )
        self.transaction_resolver = transaction_resolver or TransactionContextResolver(
            chain_provider,
            ttl_seconds=getattr(stream_settings, "transaction_context_cache_ttl_seconds", 300),
            max_entries=getattr(stream_settings, "transaction_context_cache_max_entries", 10_000),
        )
        self.defer_radar_projection = defer_radar_projection
        self._deferred_radar_rows: list[Any] = []
        self._deferred_trades: list[Any] = []
        # Dynamic streams may expose metrics=None before their transport is ready.
        self.metrics = getattr(stream, "metrics", None) or StreamMetrics()
        self._closed = False
        self.ready = asyncio.Event()
        self._db_lock = asyncio.Lock()
        self._reorg_stop = asyncio.Event()
        self._reorg_reconcile_task: asyncio.Task[None] | None = None
        self._live_edge_stable_chunks = 0
        self._last_process_outcome: dict[str, Any] = {}
        retention = (
            confirmation_depth
            + int(getattr(stream_settings, "bsc_reorg_lookback_blocks", 12)) * 2
        )
        self._seen = BlockWindowDedupe(retention)

    @staticmethod
    def _worker_instance_id() -> str:
        return os.getenv("SMMS_INSTANCE_ID", f"{socket.gethostname()}-{os.getpid()}")

    @staticmethod
    def _utc_datetime(value: datetime | None) -> datetime | None:
        """Normalize DB datetimes before comparing them with UTC ``now``.

        SQLite (and some PostgreSQL connection configurations) can return a
        timezone-aware column as a naive ``datetime``.  Treating that value as
        UTC keeps the live-head stale check deterministic across backends.
        """
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    async def record_chunk_progress(
        self,
        *,
        head_block: int,
        checkpoint_block: int,
        chunk_started_at: datetime,
        chunk_duration_seconds: float,
        transport: str,
        endpoint_index: int | None,
        counters: dict[str, Any],
    ) -> None:
        """Persist one committed catch-up unit's actual live-edge evidence."""
        if self.session is None:
            return
        lag = max(0, int(head_block) - int(checkpoint_block))
        settings = getattr(self.stream, "settings", None) or getattr(
            getattr(self.stream, "websocket", None), "settings", None
        )
        lag_threshold = int(getattr(settings, "bsc_live_lag_threshold", 3))
        required_stable_chunks = int(getattr(settings, "bsc_live_stable_chunks", 2))
        stale_after = float(getattr(settings, "bsc_head_stale_after_seconds", 30))
        self._live_edge_stable_chunks = (
            self._live_edge_stable_chunks + 1 if lag <= lag_threshold else 0
        )
        now = datetime.now(UTC)
        status = await self.session.scalar(
            select(RuntimeWorkerStatus).where(
                RuntimeWorkerStatus.worker_name == self.worker_name
            )
        )
        if status is None:
            status = RuntimeWorkerStatus(
                worker_name=self.worker_name,
                instance_id=self._worker_instance_id(),
                status="CATCHING_UP",
                transport=transport,
                provider=self.provider_name,
                updated_at=now,
            )
            self.session.add(status)
        previous_head = status.head_block
        if previous_head is None or int(head_block) > int(previous_head):
            status.head_last_advanced_at = now
        advanced_at = self._utc_datetime(status.head_last_advanced_at)
        head_staleness_seconds = (
            max(0.0, (now - advanced_at).total_seconds())
            if advanced_at is not None
            else 0.0
        )
        head_stale = head_staleness_seconds > stale_after
        phase, live_ready = ingestion_phase(
            head_block=head_block,
            checkpoint_block=checkpoint_block,
            stable_chunks=self._live_edge_stable_chunks,
            lag_threshold=lag_threshold,
            required_stable_chunks=required_stable_chunks,
            head_stale=head_stale,
        )
        status.instance_id = self._worker_instance_id()
        status.status = phase
        status.transport = transport
        status.provider = self.provider_name
        status.last_block = int(head_block)
        status.head_block = int(head_block)
        status.head_staleness_seconds = round(head_staleness_seconds, 3)
        status.checkpoint_block = int(checkpoint_block)
        status.lag = lag
        status.block_lag = lag
        status.last_processed_at = now
        status.last_success_at = now
        status.last_error = "RPC_HEAD_STALE" if head_stale else None
        status.ingestion_phase = phase
        status.live_ready = live_ready
        status.last_chunk_metrics = {
            **counters,
            "chunk_started_at": chunk_started_at.isoformat(),
            "chunk_duration_seconds": round(float(chunk_duration_seconds), 3),
            "head_block": int(head_block),
            "checkpoint_block": int(checkpoint_block),
            "block_lag": lag,
            "endpoint_index": endpoint_index,
            "transport": transport,
            "stable_chunks": self._live_edge_stable_chunks,
            "head_last_advanced_at": status.head_last_advanced_at.isoformat()
            if status.head_last_advanced_at is not None else None,
            "head_staleness_seconds": round(head_staleness_seconds, 3),
            "head_stale": head_stale,
        }
        status.updated_at = now
        await self.session.flush()
        logger.info("BSC_CHUNK_METRICS %s", json.dumps(status.last_chunk_metrics, sort_keys=True))

    async def close(self) -> None:
        self._closed = True
        await self._stop_reorg_reconciler()
        close = getattr(self.stream, "close", None)
        if close is not None:
            await close()

    async def _stop_reorg_reconciler(self) -> None:
        """Drain an entered DB transaction before cancelling the background task.

        Cancelling driver I/O can invalidate the shared connection. Once we own
        the lock, the task is outside database work and can safely be cancelled.
        Head RPC probes run outside the lock and remain immediately cancellable.
        """
        self._reorg_stop.set()
        task = self._reorg_reconcile_task
        if task is not None:
            async with self._db_lock:
                if not task.done():
                    task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self._reorg_reconcile_task = None

    async def _mark_stream_stopped(self) -> None:
        """Persist a terminal cursor without masking a preceding DB failure.

        A projection flush can leave PostgreSQL in an aborted transaction.
        The shutdown path must clear that state before touching the checkpoint,
        otherwise its secondary error hides the actual failed projection and
        prevents the next worker cycle from diagnosing it.
        """
        if self.session is None:
            return
        await self.session.rollback()
        await AsyncStreamCheckpointRepository(self.session).upsert(
            chain_id=self.chain_id,
            provider=self.checkpoint_provider,
            stream_name=self.stream_name,
            status="stopped",
        )
        await self.session.commit()

    async def flush_deferred_radar(self) -> None:
        """Coalesce Radar work after a catch-up chunk has persisted facts."""
        if not self._deferred_radar_rows or self.session is None:
            return
        from smms.radar.hooks import refresh_deferred_rows

        rows = self._deferred_radar_rows
        self._deferred_radar_rows = []
        await refresh_deferred_rows(self.session, rows)

    async def flush_deferred_snapshots(self) -> None:
        """Materialize one aggregate snapshot for each token per chunk."""
        if not self._deferred_trades or self.session is None:
            return
        from smms.market.snapshots import materialize_trade_snapshot

        selected = {}
        for trade in self._deferred_trades:
            selected[str(trade.token_address).lower()] = trade
        self._deferred_trades = []
        for trade in selected.values():
            await materialize_trade_snapshot(
                self.session, trade, as_of=getattr(trade, "timestamp", None)
            )

    async def prefetch_chunk_enrichment(self, logs: Sequence[ChainLog], config: Any) -> list[ChainLog]:
        """Overlap safe, read-only catch-up enrichment before serial DB writes.

        Timestamps and transaction callers are immutable chain facts.  The
        bounded prefetch preserves the ordered processing/checkpoint boundary
        while preventing one RPC round trip per Swap from starving the live
        edge during historical recovery.
        """
        if self.chain_provider is None:
            return list(logs)
        settings = getattr(self.stream, "settings", None)
        concurrency = max(
            1, int(getattr(settings, "bsc_catchup_rpc_concurrency", 8))
        )
        semaphore = asyncio.Semaphore(concurrency)
        timestamps: dict[int, datetime | None] = {}

        async def timestamp_for(block_number: int) -> None:
            try:
                async with semaphore:
                    timestamps[block_number] = await self.chain_provider.get_block_timestamp(block_number)
            except Exception as exc:  # noqa: BLE001 - timestamp remains best-effort enrichment
                logger.warning(
                    "BSC_TIMESTAMP_ENRICHMENT_UNAVAILABLE block=%s error=%s",
                    block_number,
                    type(exc).__name__,
                )
                timestamps[block_number] = None

        from smms.market.protocols import PancakeSwapAdapter

        adapter = PancakeSwapAdapter(config)
        transactions: dict[str, tuple[str | None, str | None]] = {}
        for log in sorted(logs, key=lambda item: (item.block_number, item.log_index)):
            if not log.topics or log.topics[0].lower() != config.swap_topic.lower():
                continue
            record = adapter.decode_swap(log)
            if record is not None:
                transactions.setdefault(
                    log.transaction_hash.lower(),
                    (
                        record.metadata.get("swap_sender"),
                        record.metadata.get("swap_recipient"),
                    ),
                )

        async def resolve_transaction(
            tx_hash: str, swap_sender: str | None, swap_recipient: str | None
        ) -> None:
            async with semaphore:
                await self.transaction_resolver.resolve(
                    tx_hash,
                    swap_sender=swap_sender,
                    swap_recipient=swap_recipient,
                )

        await asyncio.gather(
            *(timestamp_for(block) for block in sorted({
                log.block_number for log in logs if log.timestamp is None
            })),
            *(
                resolve_transaction(tx_hash, sender, recipient)
                for tx_hash, (sender, recipient) in transactions.items()
            ),
        )
        return [
            replace(log, timestamp=timestamps.get(log.block_number))
            if log.timestamp is None and timestamps.get(log.block_number) is not None
            else log
            for log in logs
        ]

    def _build_dynamic_stream(
        self, configured_factories: tuple[str, ...], configured_quotes: tuple[str, ...]
    ) -> LogStream:
        """Wrap the verified factory stream with bounded pair subscriptions."""
        from smms.chains.bsc import (
            BSCFailoverSubscription,
            BSCHTTPPollingSubscription,
            BSCWebsocketSubscription,
        )
        from smms.chains.bsc.active_pairs import DynamicPairSubscription
        from smms.market.protocols import PancakeSwapAdapter, PancakeSwapConfig

        settings = getattr(self.stream, "settings", None) or getattr(
            getattr(self.stream, "websocket", None), "settings", None
        )
        if settings is None:
            return self.stream
        config = PancakeSwapConfig(
            factory_addresses=configured_factories,
            quote_assets=configured_quotes or PancakeSwapConfig().quote_assets,
        )
        adapter = PancakeSwapAdapter(config)

        def pair_stream_factory(addresses: tuple[str, ...], _topics: tuple[str, ...]):
            if settings.bsc_ws_endpoints:
                websocket = BSCWebsocketSubscription(settings)
                if self.chain_provider is not None and settings.bsc_rpc_endpoints:
                    return BSCFailoverSubscription(
                        websocket,
                        BSCHTTPPollingSubscription(
                            self.chain_provider,
                            settings,
                        ),
                    )
                return websocket
            if self.chain_provider is not None:
                return BSCHTTPPollingSubscription(
                    self.chain_provider,
                    settings,
                )
            return self.stream

        return DynamicPairSubscription(
            self.stream,
            registry=self.active_pair_registry,
            pair_stream_factory=pair_stream_factory,
            pair_created_decoder=adapter.decode_pair_created,
            pair_topics=pair_event_topic_filter(config),
            timestamp_resolver=(
                getattr(self.chain_provider, "get_block_timestamp", None)
                if self.chain_provider is not None
                else None
            ),
            activation_replay_provider=self.chain_provider,
            activation_replay_max_blocks=getattr(settings, "bsc_reorg_lookback_blocks", 12) * 256,
            factory_addresses=config.factory_addresses,
            quote_tokens=config.quote_assets,
        )

    async def bootstrap_active_pairs(
        self, settings: Any, *, stream_name: str | None = None
    ) -> dict[str, int]:
        if self.session is None:
            return self.active_pair_registry.metrics
        factories = tuple(
            item.strip() for item in str(getattr(settings, "bsc_dex_factory_addresses", "") or "").split(",") if item.strip()
        )
        quotes = tuple(
            item.strip() for item in str(getattr(settings, "bsc_quote_tokens", "") or "").split(",") if item.strip()
        )
        if not quotes:
            from smms.market.protocols import PancakeSwapConfig
            quotes = tuple(PancakeSwapConfig().quote_assets)
        return await self.active_pair_registry.bootstrap(
            self.session,
            factory_addresses=factories,
            quote_tokens=quotes,
            stream_name=stream_name,
            checkpoint_provider=self.checkpoint_provider,
        )

    async def restore_checkpoint(self) -> Any:
        if self.session is None:
            return None
        return await AsyncStreamCheckpointRepository(self.session).get(
            self.chain_id, self.checkpoint_provider, self.stream_name
        )

    async def recovery_from_block(self, checkpoint: Any, *, lookback: int) -> int:
        """Return the lowest durable lane boundary for canonical HTTP replay."""
        repo = AsyncStreamCheckpointRepository(self.session)
        lane_rows = [
            await repo.get(
                self.chain_id,
                self.checkpoint_provider,
                f"{self.stream_name}:pair:{address}",
            )
            for address in self.active_pair_registry.addresses()
        ]
        recovery_block = min(
            (
                row.last_processed_block
                for row in (checkpoint, *lane_rows)
                if row is not None and row.last_processed_block is not None
            ),
            default=0,
        )
        return max(0, int(recovery_block or 0) - max(0, int(lookback)))

    @asynccontextmanager
    async def _reconciliation_owner(self):
        """Roll back a failed probe before another task can reuse its session."""
        async with self._db_lock:
            try:
                yield
            except BaseException:
                if self.session is not None:
                    await self.session.rollback()
                raise

    async def reconcile_reorg_window(self, latest: int, lookback: int) -> int:
        """Serialize canonical reorg reconciliation with log ingestion."""
        async with self._reconciliation_owner():
            return await self._reconcile_reorg_window_unlocked(latest, lookback)

    async def _reconcile_reorg_window_unlocked(self, latest: int, lookback: int) -> int:
        """Invalidate persisted raw events whose block hash is no longer canonical."""
        if self.session is None or self.chain_provider is None or lookback <= 0:
            return 0
        from sqlalchemy import select

        from smms.db.models import Pair
        from smms.market.pipeline import BSCMarketPipeline

        start = max(0, int(latest) - int(lookback))
        observed_at = datetime.now(UTC)
        canonical: dict[int, str] = {}
        for number in range(start, int(latest) + 1):
            getter = getattr(self.chain_provider, "get_block", None)
            if not callable(getter):
                break
            block = await getter(number, include_transactions=False)
            if block is not None and block.block_hash:
                canonical[number] = str(block.block_hash).lower()
        if not canonical:
            return 0
        from smms.alpha.work import anchor_blocks
        canonical, changed_anchors = await self.session.run_sync(lambda sync: anchor_blocks(
            sync, canonical, observed_at=observed_at, authoritative=True,
        ))
        rows = (await self.session.scalars(select(RawChainEvent).where(
            RawChainEvent.chain_id == self.chain_id,
            RawChainEvent.block_number >= start,
            RawChainEvent.block_number <= int(latest),
            RawChainEvent.removed.is_(False),
        ))).all()
        invalidated = 0
        orphan_blocks: list[int] = list(changed_anchors)
        pipeline = BSCMarketPipeline(
            self.session,
            active_pair_registry=self.active_pair_registry,
        )
        for raw in rows:
            expected = canonical.get(int(raw.block_number))
            if expected and raw.block_hash and str(raw.block_hash).lower() != expected:
                raw.removed = True
                raw.confirmation_status = "orphaned"
                await pipeline._invalidate_event(raw, invalidate_research=False)
                from smms.radar.hooks import revalidate
                await revalidate(self.session, raw)
                invalidated += 1
                orphan_blocks.append(int(raw.block_number))
        # PairCreated facts may predate raw-event persistence (for example
        # after a worker restart). Compare their stored creation hash directly
        # so an orphan cannot be restored into the live registry first.
        pairs = (await self.session.scalars(select(Pair).where(
            Pair.chain_id == self.chain_id,
            Pair.creation_block >= start,
            Pair.creation_block <= int(latest),
            Pair.canonical.is_(True),
            Pair.removed.is_(False),
        ))).all()
        for pair in pairs:
            expected = canonical.get(int(pair.creation_block)) if pair.creation_block is not None else None
            if expected and pair.creation_block_hash and str(pair.creation_block_hash).lower() != expected:
                pair.canonical = False
                pair.removed = True
                self.active_pair_registry.remove(pair.address, state="ORPHANED")
                invalidated += 1
                orphan_blocks.append(int(pair.creation_block))
        # Empty successful scans also carry fork evidence, even when no raw
        # event exists to trigger the normal invalidation path.
        from smms.alpha.work import invalidate_coverage
        from smms.db.models import AlphaCoverageReceipt
        receipts = (await self.session.scalars(select(AlphaCoverageReceipt).where(
            AlphaCoverageReceipt.canonical.is_(True),
            (AlphaCoverageReceipt.start_block.between(start, int(latest)))
            | (AlphaCoverageReceipt.end_block.between(start, int(latest))),
        ))).all()
        for receipt in receipts:
            for number, stored in ((receipt.start_block, receipt.start_hash),
                                   (receipt.end_block, receipt.end_hash)):
                if number in canonical and stored.lower() != canonical[number]:
                    orphan_blocks.append(number)
        if orphan_blocks:
            await self.session.run_sync(lambda sync: invalidate_coverage(
                sync, from_block=min(orphan_blocks), now=datetime.now(UTC),
            ))
        await self.session.flush()
        # Reconciliation is the sole canonical DB owner for this transaction;
        # persist orphan marks before the next polling/replay cycle observes
        # the same session.
        await self.session.commit()
        return invalidated

    async def _run_reorg_reconciler(self, settings: Any) -> None:
        """Single DB owner for bounded canonical block-hash reconciliation."""
        lookback = int(getattr(settings, "bsc_reorg_lookback_blocks", 12))
        interval = max(
            float(getattr(settings, "bsc_poll_interval_seconds", 2.0)),
            0.25,
        )
        while not self._closed and not self._reorg_stop.is_set():
            try:
                if self.chain_provider is not None and self.session is not None:
                    latest = await self.chain_provider.get_latest_block_number()
                    # The live subscription also owns this AsyncSession.  A
                    # reorg probe must never provision or execute on it at
                    # the same time as Raw -> Trade projection.
                    async with self._reconciliation_owner():
                        if self._closed or self._reorg_stop.is_set():
                            break
                        await self._reconcile_reorg_window_unlocked(latest, lookback)
                        checkpoint = await self.restore_checkpoint()
                        if checkpoint is not None and checkpoint.last_processed_block is not None:
                            await self.record_chunk_progress(
                                head_block=latest,
                                checkpoint_block=checkpoint.last_processed_block,
                                chunk_started_at=datetime.now(UTC),
                                chunk_duration_seconds=0,
                                transport=getattr(self.stream, "active_transport", "http"),
                                endpoint_index=getattr(self.chain_provider, "active_endpoint_index", None),
                                counters={"head_progress_probe": True},
                            )
                            await self.session.commit()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - reconciler retries on next cadence
                source_health_registry.record_failure("bsc_http", exc)
            await asyncio.sleep(interval)

    async def process_log(self, log: ChainLog) -> RawChainEvent | None:
        """Serialize canonical DB mutations against the reorg reconciler."""
        async with self._db_lock:
            return await self._process_log_unlocked(log)

    async def _process_log_unlocked(self, log: ChainLog) -> RawChainEvent | None:
        if self.session is None:
            raise RuntimeError("BSC realtime ingestion requires a database session")
        if log.removed or log.confirmation_status == "orphaned":
            # Acquire the publication fence before raw-row writes/savepoints,
            # matching the collector and reconciler's database lock order.
            from smms.alpha.work import anchor_blocks
            await self.session.run_sync(lambda sync: anchor_blocks(
                sync, {}, observed_at=datetime.now(UTC), authoritative=False,
            ))
        self._last_process_outcome = {"market_event_type": None, "trade_event": False}
        started = time.perf_counter()
        timestamp = log.timestamp
        if timestamp is None and self.chain_provider is not None:
            get_timestamp = getattr(self.chain_provider, "get_block_timestamp", None)
            if callable(get_timestamp):
                try:
                    timestamp = await get_timestamp(log.block_number)
                except Exception as exc:  # noqa: BLE001 - timestamp is enrichment, raw log is canonical
                    logger.warning(
                        "BSC_TIMESTAMP_ENRICHMENT_UNAVAILABLE block=%s error=%s",
                        log.block_number,
                        type(exc).__name__,
                    )
        status = "orphaned" if log.removed else log.confirmation_status
        event = RawChainEvent(
            chain_id=self.chain_id,
            block_number=log.block_number,
            block_hash=log.block_hash,
            transaction_hash=log.transaction_hash.lower(),
            transaction_index=log.transaction_index,
            log_index=log.log_index,
            contract_address=log.address,
            topics=list(log.topics),
            data=log.data,
            timestamp=timestamp,
            ingestion_time=datetime.now(UTC),
            provider=self.checkpoint_provider,
            confirmation_status=status,
            removed=log.removed,
            raw_payload_json=log.raw or {
                "address": log.address,
                "topics": list(log.topics),
                "data": log.data,
                "blockNumber": hex(log.block_number),
                "transactionHash": log.transaction_hash,
                "logIndex": hex(log.log_index),
            },
        )
        existing_before = await AsyncRawChainEventRepository(self.session).get_by_identity(
            self.chain_id, event.transaction_hash, event.log_index, event.block_hash
        )
        was_orphaned = bool(existing_before and (existing_before.removed or existing_before.confirmation_status == "orphaned"))
        stored = await AsyncRawChainEventRepository(self.session).add_idempotent(event)
        duplicate = stored is not event
        if duplicate:
            metrics.increment("duplicate_chain_events")
            metrics.increment("bsc_ingestion_duplicate_events_total")
        if log.removed:
            metrics.increment("orphan_events")
        await self.session.flush()
        # Replays from the HTTP boundary block are expected and are absorbed
        # by the canonical raw-event identity. Avoid running downstream
        # projections twice for an unchanged non-orphan event.
        if duplicate and (not log.removed and not was_orphaned or log.removed and was_orphaned):
            # Raw identity is durable independently from downstream
            # projections. A prior decode/DB failure may have left the raw row
            # committed without a MarketEvent; allow replay to repair that
            # boundary while preserving the fast path for complete events.
            projection_exists = await self.session.scalar(
                select(MarketEvent.id).where(
                    MarketEvent.chain_id == self.chain_id,
                    MarketEvent.tx_hash == event.transaction_hash,
                    MarketEvent.event_index == event.log_index,
                    MarketEvent.block_hash == event.block_hash,
                )
            )
            projection_complete = projection_exists is not None
            if projection_complete:
                market = await self.session.scalar(
                    select(MarketEvent).where(
                        MarketEvent.chain_id == self.chain_id,
                        MarketEvent.tx_hash == event.transaction_hash,
                        MarketEvent.event_index == event.log_index,
                        MarketEvent.block_hash == event.block_hash,
                    )
                )
                # UNKNOWN rows are deliberately replayable: a dependency or
                # ABI/configuration recovery may make an old log decodable.
                if market is not None and market.event_type == "UNKNOWN":
                    projection_complete = False
                elif market is not None and market.event_type == "TRADE":
                    trade_exists = await self.session.scalar(
                        select(TradeEvent.id).where(
                            TradeEvent.chain == "bsc",
                            TradeEvent.tx_hash == event.transaction_hash,
                            TradeEvent.event_index == event.log_index,
                            TradeEvent.block_hash == event.block_hash,
                        )
                    )
                    projection_complete = trade_exists is not None
            if projection_complete:
                self._last_process_outcome = {
                    "market_event_type": market.event_type if market is not None else None,
                    "trade_event": bool(
                        market is not None
                        and market.event_type == "TRADE"
                        and trade_exists is not None
                    ) if market is not None and market.event_type == "TRADE" else False,
                }
                self._seen.accept(log)
                return stored
        from smms.market.pipeline import BSCMarketPipeline

        try:
            from smms.market.protocols import (
                PancakeSwapAdapter,
                PancakeSwapConfig,
                ProtocolRegistry,
            )
            configured_factories = tuple(
                item.strip()
                for item in str(getattr(getattr(self.stream, "settings", None), "bsc_dex_factory_addresses", "") or "").split(",")
                if item.strip()
            )
            configured_quotes = tuple(
                item.strip().lower()
                for item in str(getattr(getattr(self.stream, "settings", None), "bsc_quote_tokens", "") or "").split(",")
                if item.strip()
            )
            config = PancakeSwapConfig(
                factory_addresses=configured_factories,
                quote_assets=configured_quotes or PancakeSwapConfig().quote_assets,
            )
            registry = ProtocolRegistry((PancakeSwapAdapter(config),))
            pair, market = await BSCMarketPipeline(
                self.session,
                registry=registry,
                # ERC-20 calls are retryable enrichment, not a prerequisite
                # for the durable Raw -> Market projection.  Keep them out
                # of this transaction; the bounded metadata backfill owns
                # RPC retries and writes failure reason codes separately.
                metadata_provider=None,
                transaction_resolver=self.transaction_resolver,
                active_pair_registry=self.active_pair_registry,
                defer_radar_projection=self.defer_radar_projection,
                deferred_radar_rows=self._deferred_radar_rows,
                deferred_trades=self._deferred_trades,
            ).process_raw_event(stored)
        except Exception as exc:
            self.metrics.record_decode_failure(exc)
            source_health_registry.record_failure("bsc_websocket" if self.provider_name == "bsc_websocket" else "bsc_http", exc)
            raise
        trade_exists = False
        if market is not None and market.event_type == "TRADE":
            trade_exists = (
                await self.session.scalar(
                    select(TradeEvent.id).where(
                        TradeEvent.chain == "bsc",
                        TradeEvent.tx_hash == event.transaction_hash,
                        TradeEvent.event_index == event.log_index,
                        TradeEvent.block_hash == event.block_hash,
                    )
                )
            ) is not None
        self._last_process_outcome = {
            "market_event_type": market.event_type if market is not None else None,
            "trade_event": trade_exists,
        }
        # The durable cursor is advanced only after the raw event and all
        # downstream projections have succeeded in this transaction.  The
        # caller commits afterwards; a projection failure therefore cannot
        # move the recovery point ahead of durable work.
        checkpoints = AsyncStreamCheckpointRepository(self.session)
        await checkpoints.upsert(
            chain_id=self.chain_id,
            provider=self.checkpoint_provider,
            stream_name=self.stream_name,
            status="running",
            block_number=log.block_number,
            log_index=log.log_index,
            transaction_hash=log.transaction_hash,
        )
        # A PairCreated event establishes the new pair's durable activation
        # boundary even before its first pair log. This prevents a quiet lane
        # from inheriting a later global checkpoint after a crash.
        if pair is not None:
            await checkpoints.upsert(
                chain_id=self.chain_id,
                provider=self.checkpoint_provider,
                stream_name=f"{self.stream_name}:pair:{pair.address.lower()}",
                status="running",
                block_number=log.block_number,
                log_index=log.log_index,
                transaction_hash=log.transaction_hash,
            )
        if self.active_pair_registry.get(log.address) is not None:
            await checkpoints.upsert(
                chain_id=self.chain_id,
                provider=self.checkpoint_provider,
                stream_name=f"{self.stream_name}:pair:{log.address.lower()}",
                status="running",
                block_number=log.block_number,
                log_index=log.log_index,
                transaction_hash=log.transaction_hash,
            )
        self._seen.accept(log)
        metrics.increment("bsc_ingestion_events_total")
        self.metrics.last_processed_block = max(
            self.metrics.last_processed_block or 0, log.block_number
        )
        self.metrics.ingestion_latency_ms = (time.perf_counter() - started) * 1000
        metrics.record_processed_block(log.block_number)
        metrics.observe("ingestion_latency_ms", self.metrics.ingestion_latency_ms)
        source_health_registry.record_success("postgresql", latency_ms=self.metrics.ingestion_latency_ms)
        return stored

    async def confirm_through(self, current_block: int) -> int:
        """Promote logs below confirmation depth; never mutate orphaned rows."""

        async with self._db_lock:
            return await self._confirm_through_unlocked(current_block)

    async def _confirm_through_unlocked(self, current_block: int) -> int:

        self.metrics.current_block = current_block
        metrics.record_chain_head(current_block)
        if self.session is None:
            return 0
        threshold = current_block - self.confirmation_depth
        if threshold < 0:
            return 0
        result = await self.session.execute(
            update(RawChainEvent)
            .where(
                RawChainEvent.chain_id == self.chain_id,
                RawChainEvent.confirmation_status == "pending",
                RawChainEvent.block_number <= threshold,
            )
            .values(confirmation_status="confirmed")
        )
        await self.session.flush()
        return int(result.rowcount or 0)

    async def run(
        self,
        *,
        address: str | Sequence[str] | None = None,
        topics: Sequence[str] | None = None,
    ) -> None:
        settings = getattr(self.stream, "settings", None) or getattr(
            getattr(self.stream, "websocket", None), "settings", None
        )
        configured_factories = tuple(
            normalize_bsc_address(item.strip())
            for item in str(getattr(settings, "bsc_dex_factory_addresses", "") or "").split(",")
            if item.strip()
        )
        configured_quotes = tuple(
            normalize_bsc_address(item.strip())
            for item in str(getattr(settings, "bsc_quote_tokens", "") or "").split(",")
            if item.strip()
        )
        dynamic_mode = bool(configured_factories and address is None and not topics)
        if not topics and (dynamic_mode or address is None):
            from smms.market.protocols import PancakeSwapConfig
            topics = (PancakeSwapConfig().pair_created_topic,)
        checkpoint = await self.restore_checkpoint()
        if dynamic_mode:
            address = configured_factories
            self.stream = self._build_dynamic_stream(configured_factories, configured_quotes)
            prepare_candidate = getattr(self.stream, "prepare_factory_candidate", None)
            if callable(prepare_candidate):
                await prepare_candidate(address=address, topics=topics)
            if self.chain_provider is not None:
                latest = await self.chain_provider.get_latest_block_number()
                lookback = int(getattr(settings, "bsc_reorg_lookback_blocks", 12))
                await self.reconcile_reorg_window(latest, lookback)
            # A fresh live-tail epoch starts from the factory edge. Loading the
            # historical active-pair registry here would turn the safe-head
            # bootstrap into a wide multi-address eth_getLogs query and can
            # block the live lane on provider limits. Historical backfill
            # continues to bootstrap and maintain those pair lanes.
            if not self.start_from_head:
                await self.bootstrap_active_pairs(settings)
            elif checkpoint is not None:
                # Preserve subscriptions across retries of this live epoch;
                # a fresh epoch still starts from the factory edge alone.
                await self.bootstrap_active_pairs(settings, stream_name=self.stream_name)
        elif not configured_factories:
            source_health_registry.set_status(
                "bsc_websocket" if "websocket" in self.provider_name else "bsc_http",
                "DEGRADED",
                details={"reason": "FACTORY_NOT_CONFIGURED"},
            )
            # Runtime discovery is fail-closed. Offline pipeline callers can
            # still decode explicit fixtures for replay and tests.
            return
        set_start_block = getattr(self.stream, "set_start_block", None)
        if callable(set_start_block) and checkpoint is not None:
            set_start_block(checkpoint.last_processed_block)
        observe = getattr(self.stream, "observe_checkpoint", None)
        if callable(observe) and checkpoint is not None:
            observe(checkpoint.last_processed_block, checkpoint.last_processed_log, checkpoint.last_processed_transaction_hash)
        # Establish and ACK the factory websocket candidate before asking HTTP
        # for its barrier. Its bounded reader covers every event during replay.
        if self.chain_provider is not None:
            latest = await self.chain_provider.get_latest_block_number()
            lookback = int(getattr(settings, "bsc_reorg_lookback_blocks", 12))
            from_block = (
                max(0, latest - lookback)
                if self.start_from_head and checkpoint is None
                else await self.recovery_from_block(checkpoint, lookback=lookback)
                if checkpoint is not None
                else max(0, latest - lookback)
            )
            scan_chunk_size = max(1, int(getattr(settings, "bsc_log_chunk_size", 10_000)))
            scan_start = from_block
            # Catch-up can contain hundreds of swaps per chunk. Persist the
            # canonical projections first and coalesce Radar refreshes below.
            self.defer_radar_projection = True
            while latest >= scan_start:
                scan_end = min(latest, scan_start + scan_chunk_size - 1)
                chunk_started_at = datetime.now(UTC)
                chunk_started = time.perf_counter()
                reset_request_metrics = getattr(
                    self.chain_provider, "reset_request_metrics", None
                )
                if callable(reset_request_metrics):
                    reset_request_metrics()
                trader_resolved_start = self.transaction_resolver.resolved
                trader_failed_start = self.transaction_resolver.failed
                factory_logs = await self.chain_provider.get_logs(
                    from_block=scan_start,
                    to_block=scan_end,
                    address=address,
                    topics=topics,
                )
                rpc_calls = list(getattr(self.chain_provider, "last_get_logs_metrics", ()))
                replayed: list[Any] = list(factory_logs)
                eligible_pair_count = 0
                pair_event_log_count = 0
                pair_created_count = 0
                if dynamic_mode:
                    from smms.market.protocols import PancakeSwapAdapter, PancakeSwapConfig

                    config = PancakeSwapConfig(
                        factory_addresses=configured_factories,
                        quote_assets=configured_quotes or PancakeSwapConfig().quote_assets,
                    )
                    adapter = PancakeSwapAdapter(config)
                    pair_addresses = set(self.active_pair_registry.addresses())
                    for factory_log in factory_logs:
                        # HTTP providers must not be trusted to honor the
                        # address filter; repeat factory admission at the
                        # protocol boundary before deriving pair lanes.
                        record = (
                            adapter.decode_pair_created(factory_log)
                            if adapter.detect(factory_log)
                            else None
                        )
                        if record is not None and record.quote_token is not None:
                            pair_addresses.add(record.address)
                            pair_created_count += 1
                    eligible_pair_count = len(pair_addresses)
                    if pair_addresses:
                        pair_logs = await self.chain_provider.get_logs(
                            from_block=scan_start,
                            to_block=scan_end,
                            address=tuple(sorted(pair_addresses)),
                            topics=pair_event_topic_filter(config),
                        )
                        pair_event_log_count = len(pair_logs)
                        replayed.extend(pair_logs)
                        rpc_calls.extend(getattr(self.chain_provider, "last_get_logs_metrics", ()))
                    replayed = await self.prefetch_chunk_enrichment(replayed, config)
                decoded_event_count = 0
                market_trade_count = 0
                trade_event_count = 0
                swap_count = 0
                for log in sorted(
                    replayed,
                    key=lambda item: (
                        item.block_number,
                        item.transaction_index if item.transaction_index is not None else -1,
                        item.log_index,
                        item.transaction_hash.lower(),
                        item.removed,
                    ),
                ):
                    if dynamic_mode and log.topics and log.topics[0].lower() == config.swap_topic.lower():
                        swap_count += 1
                    await self.process_log(log)
                    outcome = self._last_process_outcome
                    if outcome.get("market_event_type") is not None:
                        decoded_event_count += 1
                    if outcome.get("market_event_type") == "TRADE":
                        market_trade_count += 1
                    if outcome.get("trade_event"):
                        trade_event_count += 1
                await self.flush_deferred_snapshots()
                await self.flush_deferred_radar()
                await self.confirm_through(scan_end)
                checkpoints = AsyncStreamCheckpointRepository(self.session)
                await checkpoints.upsert(
                    chain_id=self.chain_id,
                    provider=self.checkpoint_provider,
                    stream_name=self.stream_name,
                    status="running",
                    block_number=scan_end,
                )
                for pair_address in self.active_pair_registry.addresses():
                    await checkpoints.upsert(
                        chain_id=self.chain_id,
                        provider=self.checkpoint_provider,
                        stream_name=f"{self.stream_name}:pair:{pair_address}",
                        status="running",
                        block_number=scan_end,
                    )
                head_block = latest
                try:
                    head_block = await self.chain_provider.get_latest_block_number()
                except Exception as exc:  # noqa: BLE001 - progress must not be rolled back for observability
                    logger.warning("BSC chunk head refresh failed: %s", type(exc).__name__)
                request_calls = list(
                    getattr(self.chain_provider, "last_request_metrics", ())
                )
                request_summary = rpc_method_summary(request_calls)
                await self.record_chunk_progress(
                    head_block=head_block,
                    checkpoint_block=scan_end,
                    chunk_started_at=chunk_started_at,
                    chunk_duration_seconds=time.perf_counter() - chunk_started,
                    transport=getattr(self.stream, "active_transport", "http"),
                    endpoint_index=getattr(self.chain_provider, "active_endpoint_index", None),
                    counters={
                        "chunk_start_block": scan_start,
                        "chunk_end_block": scan_end,
                        "chunk_block_count": scan_end - scan_start + 1,
                        "factory_log_count": len(factory_logs),
                        "pair_created_count": pair_created_count,
                        "eligible_pair_count": eligible_pair_count,
                        "pair_event_log_count": pair_event_log_count,
                        "returned_log_count": len(replayed),
                        "swap_count": swap_count,
                        "decoded_event_count": decoded_event_count,
                        "market_trade_count": market_trade_count,
                        "trade_event_count": trade_event_count,
                        "rpc_call_count": len(rpc_calls),
                        "rpc_latency_ms_total": round(
                            sum(float(item.get("latency_ms") or 0) for item in rpc_calls), 2
                        ),
                        "rpc_retry_count": sum(
                            int(item.get("retry_count") or 0) for item in rpc_calls
                        ),
                        "rpc_429_count": sum(
                            int(item.get("http_status") == 429) for item in rpc_calls
                        ),
                        "rpc_5xx_count": sum(
                            int((item.get("http_status") or 0) >= 500) for item in rpc_calls
                        ),
                        "rpc_timeout_count": sum(
                            int(item.get("outcome") == "TIMEOUT") for item in rpc_calls
                        ),
                        "rpc_request_count": len(request_calls),
                        "rpc_request_latency_ms_total": round(
                            sum(float(item.get("latency_ms") or 0) for item in request_calls), 2
                        ),
                        "rpc_request_retry_count": sum(
                            int(item.get("retry_count") or 0) for item in request_calls
                        ),
                        "rpc_request_429_count": sum(
                            int(item.get("http_status") == 429) for item in request_calls
                        ),
                        "rpc_request_5xx_count": sum(
                            int((item.get("http_status") or 0) >= 500)
                            for item in request_calls
                        ),
                        "rpc_request_timeout_count": sum(
                            int(item.get("outcome") == "TIMEOUT") for item in request_calls
                        ),
                        "rpc_method_counts": request_summary["counts"],
                        "rpc_method_latency_ms": request_summary["latency_ms"],
                        "transaction_rpc_count": sum(
                            int(item.get("method") == "eth_getTransactionByHash")
                            for item in request_calls
                        ),
                        "transaction_rpc_latency_ms_total": round(
                            sum(
                                float(item.get("latency_ms") or 0)
                                for item in request_calls
                                if item.get("method") == "eth_getTransactionByHash"
                            ),
                            2,
                        ),
                        "trader_resolved_count": self.transaction_resolver.resolved - trader_resolved_start,
                        "trader_unresolved_count": self.transaction_resolver.failed - trader_failed_start,
                    },
                )
                await self.session.commit()
                if callable(observe):
                    observe(scan_end)
                observe_pair = getattr(self.stream, "observe_pair_checkpoint", None)
                if callable(observe_pair):
                    for pair_address in self.active_pair_registry.addresses():
                        observe_pair(pair_address, scan_end)
                scan_start = scan_end + 1
            self.defer_radar_projection = False
        # A fresh live-tail epoch may finish its bounded safe-head replay
        # before any subscription log arrives.  Re-evaluate the durable
        # checkpoint once here so its second stable edge observation can
        # become LIVE; otherwise the startup CATCHING_UP projection would
        # remain stale until an unrelated future log is processed.
        if self.chain_provider is not None and self.session is not None:
            live_checkpoint = await self.restore_checkpoint()
            if live_checkpoint is not None and live_checkpoint.last_processed_block is not None:
                live_head = await self.chain_provider.get_latest_block_number()
                await self.record_chunk_progress(
                    head_block=live_head,
                    checkpoint_block=live_checkpoint.last_processed_block,
                    chunk_started_at=datetime.now(UTC),
                    chunk_duration_seconds=0,
                    transport=getattr(self.stream, "active_transport", "http"),
                    endpoint_index=getattr(self.chain_provider, "active_endpoint_index", None),
                    counters={"edge_recheck": True},
                )
                await self.session.commit()
        if self.session is not None:
            await AsyncStreamCheckpointRepository(self.session).upsert(
                chain_id=self.chain_id,
                provider=self.checkpoint_provider,
                stream_name=self.stream_name,
                status="running",
                block_number=checkpoint.last_processed_block if checkpoint else None,
                log_index=checkpoint.last_processed_log if checkpoint else None,
                transaction_hash=(
                    checkpoint.last_processed_transaction_hash if checkpoint else None
                ),
            )
            await self.session.commit()
        if (
            self.chain_provider is not None
            and self.session is not None
            and not self.start_from_head
        ):
            self._reorg_stop.clear()
            self._reorg_reconcile_task = asyncio.create_task(
                self._run_reorg_reconciler(settings)
            )
        try:
            # Startup validation, candidate ACK, replay, and checkpoint commit
            # are complete. A blocked first __anext__ is an explicit healthy-idle state.
            self.ready.set()
            async for log in self.stream.subscribe_logs(address=address, topics=topics):
                if self._closed:
                    break
                checkpoint = None
                # One owner from raw projection through checkpoint/progress commit.
                # Releasing the lock between helpers lets reconciliation commit
                # or query the same AsyncSession in another task's transaction.
                async with self._db_lock:
                    await self._process_log_unlocked(log)
                    await self._confirm_through_unlocked(log.block_number)
                    if self.session is not None:
                        await self.session.commit()
                        # Live subscriptions have no catch-up chunk callback;
                        # project edge readiness from the committed checkpoint.
                        live_checkpoint = await self.restore_checkpoint()
                        live_head = self.metrics.current_block
                        if live_head is None and self.chain_provider is not None:
                            live_head = await self.chain_provider.get_latest_block_number()
                        if (
                            live_checkpoint is not None
                            and live_checkpoint.last_processed_block is not None
                            and live_head is not None
                        ):
                            await self.record_chunk_progress(
                                head_block=int(live_head),
                                checkpoint_block=int(live_checkpoint.last_processed_block),
                                chunk_started_at=datetime.now(UTC),
                                chunk_duration_seconds=0,
                                transport=getattr(self.stream, "active_transport", "http"),
                                endpoint_index=getattr(self.chain_provider, "active_endpoint_index", None),
                                counters={"live_event_progress": True},
                            )
                            await self.session.commit()
                        checkpoint = await self.restore_checkpoint()
                observe_pair = getattr(self.stream, "observe_pair_checkpoint", None)
                if callable(observe_pair):
                    observe_pair(
                        log.address,
                        log.block_number,
                        log.log_index,
                        log.transaction_hash,
                    )
                observe = getattr(self.stream, "observe_checkpoint", None)
                if callable(observe) and checkpoint is not None:
                    observe(
                        checkpoint.last_processed_block,
                        checkpoint.last_processed_log,
                        checkpoint.last_processed_transaction_hash,
                    )
        finally:
            await self._stop_reorg_reconciler()
            async with self._db_lock:
                await self._mark_stream_stopped()

    async def status(self) -> dict[str, Any]:
        async with self._db_lock:
            return await self._status_unlocked()

    async def _status_unlocked(self) -> dict[str, Any]:
        checkpoint = await self.restore_checkpoint()
        if self.metrics.current_block is not None:
            metrics.record_chain_head(self.metrics.current_block)
        if checkpoint and checkpoint.last_processed_block is not None:
            self.metrics.last_processed_block = checkpoint.last_processed_block
            metrics.record_processed_block(checkpoint.last_processed_block)
        return {
            "chain_id": self.chain_id,
            "provider": self.provider_name,
            "stream": self.stream_name,
            "current_block": self.metrics.current_block,
            "last_processed_block": (
                checkpoint.last_processed_block if checkpoint else self.metrics.last_processed_block
            ),
            "block_lag": self.metrics.block_lag,
            "event_rate": self.metrics.event_count,
            "event_rate_per_second": self.metrics.event_rate,
            "decode_failure_rate": self.metrics.decode_failures / self.metrics.event_count if self.metrics.event_count else 0,
            "decode_failures": self.metrics.decode_failures,
            "reconnect_count": self.metrics.reconnect_count,
            "provider_errors": self.metrics.provider_errors,
            "ingestion_latency": self.metrics.ingestion_latency_ms,
            "last_event_at": self.metrics.last_event_at,
            "last_error": self.metrics.last_error,
            "last_checkpoint_at": getattr(checkpoint, "updated_at", None) if checkpoint else None,
            "active_transport": getattr(self.stream, "active_transport", "http" if "http" in self.provider_name else "ws"),
            "active_endpoint_index": getattr(getattr(self.stream, "websocket", self.stream), "active_endpoint_index", 0),
            "failover_count": getattr(self.stream, "failover_count", 0),
            "failback_count": getattr(self.stream, "failback_count", 0),
            "status": checkpoint.status if checkpoint else "not_started",
            "active_pairs": self.active_pair_registry.metrics,
        }


__all__ = ["BSCRealtimeIngestionService", "pair_event_topic_filter"]

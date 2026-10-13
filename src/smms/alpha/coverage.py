"""Small, auditable PancakeSwap V2 historical coverage scan."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import select

from smms.alpha.baseline import CoverageChunk, ResearchCoverageManifest
from smms.chains.base import ChainLog
from smms.chains.bsc.rpc import BSCProvider
from smms.config import Settings
from smms.db.database import session_scope
from smms.db.models import MarketEvent, Pair, TradeEvent
from smms.ingestion import DataUnavailable
from smms.market.pipeline import BSCMarketPipeline
from smms.market.protocols import PancakeSwapAdapter, PancakeSwapConfig


@dataclass(frozen=True)
class HistoricalCoverageConfig:
    start_block: int
    end_block: int
    output: Path
    chunk_size: int = 5_000


def _raw_event(log: ChainLog, timestamp: datetime) -> SimpleNamespace:
    return SimpleNamespace(
        removed=log.removed,
        confirmation_status="confirmed",
        contract_address=log.address,
        topics=list(log.topics),
        data=log.data,
        block_number=log.block_number,
        transaction_hash=log.transaction_hash,
        log_index=log.log_index,
        timestamp=timestamp,
        block_hash=log.block_hash,
        raw_payload_json=log.raw,
    )


def _log_timestamp(log: ChainLog) -> datetime | None:
    """Reuse the block timestamp included in historical log payloads."""
    value = log.raw.get("blockTimestamp") if isinstance(log.raw, dict) else None
    if value is None:
        return None
    try:
        seconds = int(value, 16) if isinstance(value, str) else int(value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(seconds, tz=UTC)


async def _missing_canonical_logs(
    session,
    *,
    pair_logs: list[ChainLog],
    swap_logs: list[ChainLog],
    adapter: PancakeSwapAdapter,
) -> list[ChainLog]:
    """Return only logs whose canonical projection is absent.

    Historical scans are idempotent: an already-persisted canonical fact is
    durable evidence of this run's persistence step. Missing facts are sent
    through the canonical pipeline. Batch identity checks avoid re-projecting
    thousands of unchanged Swap rows one by one.
    """
    pair_addresses = []
    for log in pair_logs:
        record = adapter.decode_pair_created(log)
        if record is None:
            raise DataUnavailable("coverage PairCreated decode failed")
        pair_addresses.append(record.address.lower())
    existing_pairs = set()
    if pair_addresses:
        existing_pairs = {
            address.lower()
            for address in await session.scalars(
                select(Pair.address).where(Pair.chain_id == 56, Pair.address.in_(pair_addresses))
            )
        }
    # Read the durable facts through the indexed chunk boundary, then compare
    # exact identities in Python.  A large composite ``IN`` over tx/index/hash
    # has no matching index in older databases and can turn an idempotent scan
    # into a very long transaction.
    block_numbers = [log.block_number for log in swap_logs]
    existing_swaps = set()
    if block_numbers:
        rows = await session.execute(
            select(TradeEvent.tx_hash, TradeEvent.event_index, TradeEvent.block_hash).where(
                TradeEvent.chain == "bsc",
                TradeEvent.block_number >= min(block_numbers),
                TradeEvent.block_number <= max(block_numbers),
            )
        )
        existing_swaps = {
            (tx_hash.lower(), event_index, block_hash)
            for tx_hash, event_index, block_hash in rows
        }
    return [
        log
        for log in pair_logs
        if adapter.decode_pair_created(log).address.lower() not in existing_pairs
    ] + [
        log
        for log in swap_logs
        if (log.transaction_hash.lower(), log.log_index, log.block_hash) not in existing_swaps
    ]


async def _known_block_timestamps(
    session,
    *,
    start_block: int,
    end_block: int,
) -> dict[int, datetime]:
    """Reuse durable event timestamps; query RPC only for blocks not in the DB."""
    statements = (
        select(MarketEvent.block_number, MarketEvent.timestamp).where(
            MarketEvent.chain_id == 56,
            MarketEvent.block_number >= start_block,
            MarketEvent.block_number <= end_block,
            MarketEvent.timestamp.is_not(None),
        ),
        select(TradeEvent.block_number, TradeEvent.timestamp).where(
            TradeEvent.chain == "bsc",
            TradeEvent.block_number >= start_block,
            TradeEvent.block_number <= end_block,
        ),
        select(Pair.creation_block, Pair.creation_timestamp).where(
            Pair.chain_id == 56,
            Pair.creation_block >= start_block,
            Pair.creation_block <= end_block,
            Pair.creation_timestamp.is_not(None),
        ),
    )
    timestamps: dict[int, datetime] = {}
    for statement in statements:
        rows = await session.execute(statement)
        timestamps.update(
            {
                int(block): timestamp
                for block, timestamp in rows
                if block is not None and timestamp is not None
            }
        )
    return timestamps


async def build_coverage_manifest(
    settings: Settings,
    config: HistoricalCoverageConfig,
) -> ResearchCoverageManifest:
    """Scan PairCreated and eligible-pair Swap logs, persisting each chunk.

    A chunk is committed only after all logs decode and persist. Any RPC,
    timestamp, decode, or persistence error marks that chunk incomplete and
    prevents a VERIFIED manifest.
    """
    if config.start_block < 0 or config.end_block < config.start_block:
        raise ValueError("invalid historical block range")
    if config.chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    factories = tuple(
        item.strip().lower()
        for item in str(settings.bsc_dex_factory_addresses or "").split(",")
        if item.strip()
    )
    if len(factories) != 1:
        raise ValueError("coverage scan requires exactly one configured PancakeSwap factory")
    config_adapter = PancakeSwapConfig(factory_addresses=factories)
    adapter = PancakeSwapAdapter(config_adapter)
    provider = BSCProvider(
        settings,
        log_chunk_size=config.chunk_size,
        verify_on_use=True,
    )
    started_at = datetime.now(UTC)
    chunks: list[CoverageChunk] = []
    pair_addresses: set[str] = set()
    start_timestamp = await provider.get_block_timestamp(config.start_block)
    end_timestamp = await provider.get_block_timestamp(config.end_block)
    if start_timestamp is None or end_timestamp is None:
        raise DataUnavailable("coverage range block timestamps unavailable")
    try:
        async with session_scope(settings) as session:
            # Coverage needs Pair/Swap facts, not best-effort metadata or one
            # transaction RPC per Swap. Indexed participants remain explicitly
            # marked by the pipeline when no transaction caller is available.
            pipeline = BSCMarketPipeline(
                session,
                defer_radar_projection=True,
                deferred_trades=[],
                allow_indexed_trader_fallback=True,
            )
            block_timestamps = await _known_block_timestamps(
                session,
                start_block=config.start_block,
                end_block=config.end_block,
            )
            block = config.start_block
            while block <= config.end_block:
                chunk_end = min(config.end_block, block + config.chunk_size - 1)
                pair_count = 0
                swap_count = 0
                decoded_count = 0
                error: str | None = None
                successful = False
                persisted = False
                decode_complete = True
                pair_query_success = False
                swap_query_success = False
                try:
                    factory_logs = await provider.get_logs(
                        from_block=block,
                        to_block=chunk_end,
                        address=factories[0],
                        topics=(config_adapter.pair_created_topic,),
                    )
                    pair_query_success = True
                    for log in factory_logs:
                        record = adapter.decode_pair_created(log) if adapter.detect(log) else None
                        if record is None:
                            decode_complete = False
                            continue
                        if record.quote_token is not None:
                            pair_addresses.add(record.address.lower())
                    pair_count = len(factory_logs)
                    addresses = tuple(sorted(pair_addresses))
                    swap_logs = (
                        await provider.get_logs(
                            from_block=block,
                            to_block=chunk_end,
                            address=addresses,
                            topics=(config_adapter.swap_topic,),
                        )
                        if addresses
                        else []
                    )
                    swap_query_success = True
                    swap_count = len(swap_logs)
                    for log in swap_logs:
                        if adapter.decode_swap(log) is None:
                            raise DataUnavailable("coverage Swap decode failed")
                    decoded_count = len(swap_logs)
                    async with session.begin_nested():
                        missing_logs = await _missing_canonical_logs(
                            session,
                            pair_logs=factory_logs,
                            swap_logs=swap_logs,
                            adapter=adapter,
                        )
                        replay = sorted(
                            missing_logs,
                            key=lambda item: (item.block_number, item.log_index, item.transaction_hash),
                        )
                        for log in replay:
                            timestamp = block_timestamps.get(log.block_number) or _log_timestamp(log)
                            if timestamp is None:
                                timestamp = await provider.get_block_timestamp(log.block_number)
                            if timestamp is None:
                                raise DataUnavailable("coverage log block timestamp unavailable")
                            block_timestamps[log.block_number] = timestamp
                            await pipeline.process_raw_event(_raw_event(log, timestamp))
                        await session.flush()
                    successful = True
                    persisted = True
                except Exception as exc:  # noqa: BLE001 - manifest records exact failed chunk
                    error = f"{type(exc).__name__}: {exc}"[:500]
                    decode_complete = False
                chunks.append(
                    CoverageChunk(
                        start_block=block,
                        end_block=chunk_end,
                        successful=successful,
                        persisted=persisted,
                        decode_complete=decode_complete,
                        raw_log_count=pair_count + swap_count,
                        decoded_swap_count=decoded_count,
                        pair_created_query_success=pair_query_success,
                        swap_query_success=swap_query_success,
                        pair_created_raw_count=pair_count,
                        swap_raw_count=swap_count,
                        error=error,
                    )
                )
                block = chunk_end + 1
            await session.commit()
    finally:
        # BSCProvider owns short-lived clients per request when no client is
        # injected; there is no persistent client to close here.
        pass
    completed_at = datetime.now(UTC)
    manifest = ResearchCoverageManifest.from_chunks(
        chain_id=56,
        protocol="pancakeswap-v2",
        factory=factories[0],
        start_block=config.start_block,
        end_block=config.end_block,
        start_timestamp=start_timestamp,
        end_timestamp=end_timestamp,
        pair_created_query_complete=all(chunk.pair_created_query_success for chunk in chunks),
        swap_query_complete=all(chunk.swap_query_success for chunk in chunks),
        relevant_pair_count=len(pair_addresses),
        rpc_source=provider.url,
        query_parameters={"chunk_size": config.chunk_size, "factory": factories[0]},
        started_at=started_at,
        completed_at=completed_at,
        chunks=chunks,
        relevant_pair_addresses=pair_addresses,
    )
    manifest.write_json(config.output)
    return manifest


def run_coverage_scan(settings: Settings, config: HistoricalCoverageConfig) -> ResearchCoverageManifest:
    return asyncio.run(build_coverage_manifest(settings, config))

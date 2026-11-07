"""Leased confirmed HTTP scans; receipts and canonical facts commit atomically."""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import or_, select, update

from smms.alpha.work import SWAP_TOPIC, anchor_blocks, repair_work
from smms.db.models import AlphaCoverageJob, AlphaCoverageReceipt, Pair, RawChainEvent, TradeEvent
from smms.db.repositories.stream import AsyncRawChainEventRepository
from smms.evaluation import utc
from smms.market.pipeline import BSCMarketPipeline
from smms.market.protocols import PancakeSwapAdapter


@dataclass(frozen=True)
class Claim:
    job_id: int
    pair_id: int
    pair_address: str
    creation_hash: str
    creation_block: int
    generation: int
    start: int
    lease: str
    observed_at: datetime


def claim_work(session, *, now: datetime, factories: set[str]) -> Claim | None:
    repair_work(session, batch_size=128)
    job = session.scalar(select(AlphaCoverageJob).where(
        AlphaCoverageJob.status == "PENDING", AlphaCoverageJob.next_attempt_at <= now,
        or_(AlphaCoverageJob.leased_until.is_(None), AlphaCoverageJob.leased_until <= now),
    ).order_by(AlphaCoverageJob.next_attempt_at, AlphaCoverageJob.id).limit(1)
        .with_for_update(skip_locked=True))
    if job is None:
        return None
    pair = session.get(Pair, job.pair_id)
    if not pair.canonical or pair.removed:
        job.status = "INVALIDATED"
        return None
    if (pair.factory_address or "").lower() not in factories or pair.dex != "pancakeswap":
        job.last_error = "UNSUPPORTED_FACTORY"
        job.next_attempt_at = now + timedelta(minutes=5)
        return None
    if pair.creation_block is None or not pair.creation_block_hash or job.next_block is None:
        job.last_error = "MISSING_CREATION_BLOCK"
        job.next_attempt_at = now + timedelta(minutes=5)
        return None
    job.lease_token = uuid4().hex
    job.leased_until = now + timedelta(minutes=5)
    return Claim(job.id, pair.id, pair.address.lower(), pair.creation_block_hash.lower(),
                 pair.creation_block, job.generation, job.next_block, job.lease_token, now)


def validate_scan(logs, *, pair_address, start, end, headers):
    """Fail closed before any database projection; do not silently drop bad logs."""
    seen = set()
    adapter = PancakeSwapAdapter()
    for log in logs:
        if (log.address.lower() != pair_address.lower() or not log.topics
                or log.topics[0].lower() != SWAP_TOPIC or not start <= log.block_number <= end
                or log.removed or not log.block_hash):
            raise ValueError("scan log scope mismatch")
        header = headers.get(log.block_number)
        if header is None or header.block_hash.lower() != log.block_hash.lower():
            raise ValueError("scan log hash mismatch")
        if log.identity in seen or adapter.decode_swap(log) is None:
            raise ValueError("scan log duplicate or decode failure")
        seen.add(log.identity)


async def fetch_scan(provider, claim: Claim, *, confirmations: int, chunk_size: int):
    if confirmations < 1 or not 1 <= chunk_size <= 5000:
        raise ValueError("coverage requires confirmations and bounded chunks")
    head = await provider.get_latest_block_number()
    end = min(head - confirmations, claim.start + chunk_size - 1)
    if end < claim.start:
        return None
    first = await provider.get_block(claim.start)
    last = first if end == claim.start else await provider.get_block(end)
    if (first is None or last is None or first.number != claim.start or last.number != end
            or not first.raw.get("parentHash") or utc(last.timestamp) < utc(first.timestamp)):
        raise ValueError("scan boundary unavailable")
    if claim.start == claim.creation_block and first.block_hash.lower() != claim.creation_hash:
        raise ValueError("scan creation fork mismatch")
    logs = await provider.get_logs(from_block=claim.start, to_block=end,
                                   address=claim.pair_address, topics=[SWAP_TOPIC])
    headers = {claim.start: first, end: last}
    for number in sorted({log.block_number for log in logs} - headers.keys()):
        headers[number] = await provider.get_block(number)
    validate_scan(logs, pair_address=claim.pair_address, start=claim.start, end=end, headers=headers)
    # Resolve callers outside the DB transaction. Failure is retryable, never a
    # claim that the query produced a complete canonical economic trade set.
    callers = {}
    enriched = []
    for log in sorted(logs, key=lambda value: (value.block_number, value.log_index)):
        if log.transaction_hash not in callers:
            tx = await provider.get_transaction(log.transaction_hash)
            if tx is None or not tx.from_address or tx.block_number != log.block_number:
                raise ValueError("scan caller unavailable")
            callers[log.transaction_hash] = tx.from_address
        enriched.append(replace(log, timestamp=headers[log.block_number].timestamp,
                                raw={**log.raw, "transaction_from": callers[log.transaction_hash]}))
    # Re-read without the timestamp cache after RPC queries, including zero logs.
    check = await provider.get_block(end)
    if check is None or check.block_hash.lower() != last.block_hash.lower():
        raise ValueError("scan boundary changed")
    return first, last, enriched, headers


async def persist_scan(session, claim: Claim, scan, *, now: datetime) -> bool:
    """Caller owns commit/rollback. A stale lease cannot publish any evidence."""
    if scan is not None:
        # Same lock order as reconciliation: block fences, then job, then facts.
        # This covers first/zero-log scans with no previous receipt to invalidate.
        _, _, _, headers = scan
        expected = {number: header.block_hash.lower() for number, header in headers.items()}
        effective, _ = await session.run_sync(lambda sync: anchor_blocks(
            sync, expected, observed_at=claim.observed_at, authoritative=False,
        ))
        if effective != expected:
            await session.run_sync(lambda sync: retry_claim(
                sync, claim, now=now, error="CANONICAL_FENCE_CONFLICT",
            ))
            return False
    job = await session.scalar(select(AlphaCoverageJob).where(
        AlphaCoverageJob.id == claim.job_id,
    ).with_for_update())
    if (job is None or job.generation != claim.generation or job.lease_token != claim.lease
            or job.status != "PENDING" or job.next_block != claim.start
            or job.leased_until is None or utc(job.leased_until) <= utc(now)):
        return False
    pair = await session.get(Pair, claim.pair_id)
    if not pair.canonical or pair.removed or (pair.creation_block_hash or "").lower() != claim.creation_hash:
        return False
    if scan is None:
        job.lease_token = job.leased_until = None
        job.next_attempt_at = now + timedelta(seconds=15)
        return False
    first, last, logs, headers = scan
    validate_scan(logs, pair_address=claim.pair_address, start=claim.start, end=last.number,
                  headers=headers)
    if claim.start > claim.creation_block:
        previous = await session.scalar(select(AlphaCoverageReceipt).where(
            AlphaCoverageReceipt.job_id == job.id, AlphaCoverageReceipt.generation == job.generation,
            AlphaCoverageReceipt.canonical.is_(True), AlphaCoverageReceipt.end_block == claim.start - 1,
        ))
        if previous is None or first.raw["parentHash"].lower() != previous.end_hash.lower():
            raise ValueError("scan parent continuity mismatch")
    pipeline = BSCMarketPipeline(session, defer_radar_projection=True, deferred_trades=[])
    repo = AsyncRawChainEventRepository(session)
    # Establish an outer write before idempotency savepoints. SQLite's legacy
    # transaction mode otherwise releases the first SAVEPOINT as its own commit.
    await session.execute(update(AlphaCoverageJob).where(AlphaCoverageJob.id == job.id).values(last_error=None))
    for log in logs:
        raw = await repo.add_idempotent(RawChainEvent(
            chain_id=56, block_number=log.block_number, block_hash=log.block_hash,
            transaction_hash=log.transaction_hash, log_index=log.log_index,
            transaction_index=log.transaction_index, contract_address=log.address,
            topics=list(log.topics), data=log.data, timestamp=log.timestamp,
            provider="alpha-confirmed-http", confirmation_status="confirmed",
            removed=False, raw_payload_json=log.raw,
        ))
        await pipeline.process_raw_event(raw)
        await session.flush()
        trade = await session.scalar(select(TradeEvent.id).where(
            TradeEvent.chain == "bsc", TradeEvent.pair_address == claim.pair_address,
            TradeEvent.tx_hash == log.transaction_hash.lower(), TradeEvent.event_index == log.log_index,
            TradeEvent.block_hash == log.block_hash.lower(),
            TradeEvent.canonical.is_(True), TradeEvent.removed.is_(False),
        ))
        if trade is None:
            raise ValueError("scan canonical trade persistence incomplete")
    session.add(AlphaCoverageReceipt(
        job_id=job.id, generation=job.generation, start_block=first.number, end_block=last.number,
        start_hash=first.block_hash.lower(), end_hash=last.block_hash.lower(),
        start_parent_hash=first.raw["parentHash"].lower(), start_at=first.timestamp, end_at=last.timestamp,
        raw_count=len(logs), decoded_count=len(logs), completed_at=now,
        query_json={"chain_id": 56, "pair": claim.pair_address, "topic": SWAP_TOPIC,
                    "protocol": "pancakeswap", "confirmed": True},
    ))
    job.next_block = last.number + 1
    job.status = "COMPLETE" if utc(last.timestamp) >= utc(job.target_at) else "PENDING"
    job.next_attempt_at = now
    job.lease_token = job.leased_until = job.last_error = None
    await session.flush()
    return True


def retry_claim(session, claim: Claim, *, now: datetime, error: str):
    job = session.scalar(select(AlphaCoverageJob).where(
        AlphaCoverageJob.id == claim.job_id,
    ).with_for_update())
    if job and job.generation == claim.generation and job.lease_token == claim.lease:
        job.lease_token = job.leased_until = None
        job.last_error = error[:64]
        job.next_attempt_at = now + timedelta(seconds=60)

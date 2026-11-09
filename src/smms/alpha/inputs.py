"""Shared canonical inputs for one bounded set of due Alpha snapshots."""

from bisect import bisect_left, bisect_right
from collections import defaultdict

from sqlalchemy import and_, or_, select

from smms.db.facts import canonical_fact_conditions
from smms.db.models import AlphaCoverageJob, AlphaCoverageReceipt, Pair, TradeEvent
from smms.evaluation import utc

from .policy import TRACKING_HORIZON
from .work import coverage_proof

PREDICATES_PER_QUERY = 200


class AlphaInputs:
    def __init__(self, session, rows, *, now):
        self.pairs, self.jobs, self.receipts, self.entry_proofs = {}, {}, defaultdict(list), {}
        self.trades, self.times = defaultdict(list), {}
        if not rows:
            return
        addresses = {row.pair_address for row in rows}
        self.pairs = {row.address: row for row in session.scalars(select(Pair).where(
            Pair.chain_id == 56, Pair.address.in_(addresses),
        ).execution_options(populate_existing=True))}
        self.jobs = {row.pair_id: row for row in session.scalars(select(AlphaCoverageJob).where(
            AlphaCoverageJob.pair_id.in_({row.id for row in self.pairs.values()}),
        ).execution_options(populate_existing=True))}
        for receipt in session.scalars(select(AlphaCoverageReceipt).join(
            AlphaCoverageJob, AlphaCoverageJob.id == AlphaCoverageReceipt.job_id,
        ).where(AlphaCoverageReceipt.job_id.in_({row.id for row in self.jobs.values()}),
                AlphaCoverageReceipt.generation == AlphaCoverageJob.generation,
                AlphaCoverageReceipt.canonical.is_(True), AlphaCoverageReceipt.completed_at <= now)
                .order_by(AlphaCoverageReceipt.start_block, AlphaCoverageReceipt.id)
                .execution_options(populate_existing=True)):
            self.receipts[receipt.job_id].append(receipt)
        ranges = defaultdict(list)
        for row in rows:
            pair = self.pairs.get(row.pair_address)
            job = self.jobs.get(pair.id) if pair else None
            if pair is None or job is None or row.coverage_generation != job.generation:
                continue
            proof = coverage_proof(session, pair, start=row.pair_created_at, end=row.snapshot_at,
                as_of=now, job=job, receipts=self.receipts[job.id])
            self.entry_proofs[row.id] = proof
            if proof["status"] == "VERIFIED":
                start = row.pair_created_at if row.alpha_score is None else row.snapshot_at
                ranges[(row.pair_address, row.token_address)].append((utc(start), min(utc(now), utc(row.snapshot_at) + TRACKING_HORIZON)))
        merged = []
        for (pair, token), windows in ranges.items():
            current = None
            for start, end in sorted(windows):
                if current is not None and start <= current[1]:
                    current = current[0], max(current[1], end)
                else:
                    if current is not None:
                        merged.append((pair, token, *current))
                    current = start, end
            if current is not None:
                merged.append((pair, token, *current))
        for offset in range(0, len(merged), PREDICATES_PER_QUERY):
            chunk = merged[offset:offset + PREDICATES_PER_QUERY]
            for trade in session.scalars(select(TradeEvent).where(TradeEvent.chain == "bsc",
                *canonical_fact_conditions(TradeEvent), or_(*(and_(TradeEvent.pair_address == pair,
                    TradeEvent.token_address == token, TradeEvent.timestamp >= start, TradeEvent.timestamp <= end)
                    for pair, token, start, end in chunk)))
                    .order_by(TradeEvent.timestamp, TradeEvent.id).execution_options(populate_existing=True)):
                self.trades[(trade.pair_address, trade.token_address)].append(trade)
        self.times = {key: [utc(trade.timestamp) for trade in trades] for key, trades in self.trades.items()}

    def window(self, row, *, start, end, exclusive_start=False):
        key = row.pair_address, row.token_address
        times = self.times.get(key, [])
        left = (bisect_right if exclusive_start else bisect_left)(times, utc(start))
        right = bisect_right(times, utc(end))
        return self.trades[key][left:right]

    def proof(self, session, pair, *, start, end, now):
        job = self.jobs.get(pair.id)
        return coverage_proof(session, pair, start=start, end=end, as_of=now,
            job=job, receipts=self.receipts[job.id] if job else [])

    def proof_receipts(self, pair):
        job = self.jobs.get(pair.id)
        return self.receipts[job.id] if job else []

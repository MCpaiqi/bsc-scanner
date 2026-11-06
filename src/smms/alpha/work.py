"""Transactional task registration and block-contiguous coverage proofs."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from smms.alpha.policy import SCORE_VERSION, SNAPSHOT_WINDOWS, TRACKING_HORIZON
from smms.db.models import (
    AlphaBlockAnchor,
    AlphaCoverageJob,
    AlphaCoverageReceipt,
    AlphaSnapshot,
    AlphaWorkCursor,
    Pair,
)
from smms.evaluation import utc
from smms.market.protocols import PancakeSwapConfig

SWAP_TOPIC = PancakeSwapConfig().swap_topic.lower()


def _insert(session, model, values, keys):
    if session.bind.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    session.execute(insert(model).values(**values).on_conflict_do_nothing(index_elements=keys))


def register_pair(session: Session, pair: Pair) -> None:
    """Enqueue in the same transaction as discovery; imports are repaired cyclically."""
    if (pair.chain_id != 56 or not pair.canonical or pair.removed
            or pair.creation_timestamp is None or not pair.quote_token):
        return
    token = next((a for a in (pair.token0_address, pair.token1_address)
                  if a and a.lower() != pair.quote_token.lower()), None)
    if token is None:
        return
    t0 = utc(pair.creation_timestamp)
    _insert(session, AlphaCoverageJob, {
        "pair_id": pair.id, "generation": 0, "next_block": pair.creation_block,
        "target_at": t0 + timedelta(seconds=max(SNAPSHOT_WINDOWS)) + TRACKING_HORIZON,
        "next_attempt_at": t0, "status": "PENDING",
    }, ["pair_id"])
    job = session.scalar(select(AlphaCoverageJob).where(AlphaCoverageJob.pair_id == pair.id))
    if job.next_block is None and pair.creation_block is not None and job.status == "PENDING":
        job.next_block, job.next_attempt_at = pair.creation_block, t0
        job.last_error = None
    if job.status == "INVALIDATED":
        # Only canonical rediscovery may resume this job. Avoid waiting on a
        # reconciler while holding another pair's lock during the repair sweep.
        job = session.scalar(select(AlphaCoverageJob).where(
            AlphaCoverageJob.pair_id == pair.id, AlphaCoverageJob.status == "INVALIDATED",
        ).with_for_update(skip_locked=True).execution_options(populate_existing=True))
        if job is None:
            return
        job.generation += 1
        job.status, job.next_block = "PENDING", pair.creation_block
        job.target_at = t0 + timedelta(seconds=max(SNAPSHOT_WINDOWS)) + TRACKING_HORIZON
        job.next_attempt_at = t0
        job.lease_token = job.leased_until = job.last_error = None
    for seconds in SNAPSHOT_WINDOWS:
        latest = session.scalar(select(AlphaSnapshot).where(
            AlphaSnapshot.chain == "bsc", AlphaSnapshot.token_address == token.lower(),
            AlphaSnapshot.pair_address == pair.address.lower(), AlphaSnapshot.observation_seconds == seconds,
            AlphaSnapshot.score_version == SCORE_VERSION,
        ).order_by(AlphaSnapshot.input_revision.desc(), AlphaSnapshot.id.desc()).limit(1))
        if latest is not None:
            same_generation = latest.coverage_generation == job.generation
            pending = latest.task_status == "PENDING_INPUT" and latest.alpha_score is None
            verified = (latest.task_status in {"TRACKING", "COMPLETE"}
                        and (latest.input_manifest or {}).get("schema_version") == "alpha-inputs-v1")
            if same_generation and (pending or verified):
                continue
            if latest.task_status != "INVALIDATED":
                invalidate_snapshot(latest, reason="SOURCE_GENERATION_OR_MANIFEST_CHANGED", now=datetime.now(UTC))
        at = t0 + timedelta(seconds=seconds)
        old_outcome = latest.outcome_json or {} if latest is not None else {}
        pinned = {key: old_outcome[key] for key in ("minimum_quote", "policy_id", "label_version")
                  if key in old_outcome}
        _insert(session, AlphaSnapshot, {
            "chain": "bsc", "token_address": token.lower(), "pair_address": pair.address.lower(),
            "pair_created_at": t0, "snapshot_at": at, "age_seconds": seconds,
            "observation_seconds": seconds, "alpha_score": None, "score_version": SCORE_VERSION,
            "scored_at": at, "task_status": "PENDING_INPUT", "next_evaluation_at": at,
            "input_revision": latest.input_revision + 1 if latest is not None else 0,
            "supersedes_id": latest.id if latest is not None else None,
            "coverage_generation": job.generation, "input_manifest": {},
            "outcome_json": {**pinned, **{f"reached_{n}x": None for n in (2, 3, 5, 10)},
                             "tracking_complete": False},
        }, ["chain", "token_address", "pair_address", "observation_seconds", "score_version", "input_revision"])


def repair_work(session: Session, *, batch_size: int) -> None:
    _insert(session, AlphaWorkCursor, {"version": SCORE_VERSION, "last_pair_id": 0}, ["version"])
    cursor = session.scalar(select(AlphaWorkCursor).where(
        AlphaWorkCursor.version == SCORE_VERSION,
    ).with_for_update())
    pairs = session.scalars(select(Pair).where(
        Pair.chain_id == 56, Pair.id > cursor.last_pair_id,
    ).order_by(Pair.id).limit(batch_size)).all()
    for pair in pairs:
        register_pair(session, pair)
    cursor.last_pair_id = pairs[-1].id if pairs else 0
    session.flush()


def coverage_proof(session: Session, pair: Pair, *, start: datetime, end: datetime,
                   as_of: datetime) -> dict:
    """Require exact query scope, canonical generation and linked adjacent ranges.

    A boundary after the requested end proves the whole interval, including a
    successful zero-log query. Neither elapsed wall time nor stored trades does.
    """
    missing = {"status": "MISSING", "receipt_ids": []}
    if not pair.canonical or pair.removed or pair.creation_block is None or not pair.creation_block_hash:
        return missing
    job = session.scalar(select(AlphaCoverageJob).where(AlphaCoverageJob.pair_id == pair.id))
    if job is None or job.status == "INVALIDATED":
        return missing
    rows = session.scalars(select(AlphaCoverageReceipt).where(
        AlphaCoverageReceipt.job_id == job.id, AlphaCoverageReceipt.generation == job.generation,
        AlphaCoverageReceipt.canonical.is_(True), AlphaCoverageReceipt.completed_at <= as_of,
    ).order_by(AlphaCoverageReceipt.start_block)).all()
    ids, previous, initial = [], None, None
    for row in rows:
        query = row.query_json or {}
        if (query != {"chain_id": 56, "pair": pair.address.lower(), "topic": SWAP_TOPIC,
                      "protocol": "pancakeswap", "confirmed": True}
                or row.raw_count != row.decoded_count or row.raw_count < 0
                or row.end_block < row.start_block or utc(row.end_at) < utc(row.start_at)
                or utc(row.completed_at) < utc(row.end_at)
                or not row.start_hash or not row.end_hash or not row.start_parent_hash):
            return missing
        if previous is None:
            if row.start_block != pair.creation_block or row.start_hash.lower() != pair.creation_block_hash.lower():
                return missing
            initial = utc(row.start_at)
            if initial != utc(pair.creation_timestamp) or initial > utc(start):
                return missing
        elif (row.start_block != previous.end_block + 1
              or row.start_parent_hash.lower() != previous.end_hash.lower()
              or utc(row.start_at) < utc(previous.end_at)):
            return missing
        ids.append(row.id)
        if utc(row.end_at) >= utc(end):
            return {"status": "VERIFIED", "generation": job.generation, "receipt_ids": ids,
                    "start_at": initial.isoformat(), "end_at": utc(row.end_at).isoformat(),
                    "end_hash": row.end_hash, "end_block": row.end_block}
        previous = row
    return missing


def invalidate_coverage(session: Session, *, from_block: int, now: datetime) -> int:
    """Conservatively invalidate every later proof; never retain a stale negative."""
    from smms.research.state import invalidate_derived

    invalidate_derived(session, reason="CANONICAL_INVALIDATION", cause={"from_block": from_block,
                                                                      "observed_at": utc(now).isoformat()})
    jobs = session.scalars(select(AlphaCoverageJob).join(Pair).where(
        Pair.chain_id == 56,
        (AlphaCoverageJob.id.in_(select(AlphaCoverageReceipt.job_id).where(
            AlphaCoverageReceipt.canonical.is_(True), AlphaCoverageReceipt.end_block >= from_block,
        ))) | (AlphaCoverageJob.lease_token.is_not(None))
        | Pair.canonical.is_(False) | Pair.removed.is_(True) | (Pair.creation_block >= from_block),
    ).order_by(AlphaCoverageJob.id).with_for_update(of=AlphaCoverageJob)).all()
    for job in jobs:
        pair = session.get(Pair, job.pair_id)
        session.execute(update(AlphaCoverageReceipt).where(
            AlphaCoverageReceipt.job_id == job.id,
        ).values(canonical=False))
        job.generation += 1
        job.next_block = pair.creation_block
        job.next_attempt_at, job.status = now, "PENDING" if pair.canonical and not pair.removed else "INVALIDATED"
        job.lease_token = job.leased_until = None
        for row in session.scalars(select(AlphaSnapshot).where(
            AlphaSnapshot.chain == "bsc", AlphaSnapshot.pair_address == pair.address,
            AlphaSnapshot.score_version == SCORE_VERSION,
        )):
            invalidate_snapshot(row, reason="CANONICAL_REORG", now=now)
        register_pair(session, pair)
    return len(jobs)


def invalidate_snapshot(row, *, reason, now):
    if row.task_status == "INVALIDATED":
        return
    previous = dict(row.outcome_json or {})
    row.task_status, row.next_evaluation_at = "INVALIDATED", None
    row.outcome_json = {**previous, "invalidated_outcome": previous,
                        "coverage_invalidated_at": utc(now).isoformat(),
                        "tracking_complete": False, "invalid_reason": reason,
                        **{f"reached_{n}x": None for n in (2, 3, 5, 10)}}


def invalidate_removed_source(session, *, block_number, block_hash, now, force=False):
    """Fence unknown replacement hashes until canonical reconciliation supplies one."""
    if block_number is None:
        return invalidate_coverage(session, from_block=0, now=now)
    expected = block_hash.lower()
    effective, _ = anchor_blocks(session, {block_number: expected}, observed_at=now, authoritative=False)
    if effective[block_number] != expected and not force:
        # Already fenced, or canonical reconciliation knows a different fork.
        # Duplicate notifications without newly changed facts add no evidence.
        return 0
    if effective[block_number] == expected:
        anchor_blocks(session, {block_number: f"orphaned:{expected}"}, observed_at=now, authoritative=True)
    return invalidate_coverage(session, from_block=block_number, now=now)


def anchor_blocks(session: Session, blocks: dict[int, str], *, observed_at: datetime,
                  authoritative: bool) -> tuple[dict[int, str], list[int]]:
    """Lock the canonical publication fence before jobs in both owners.

    A short DB-only publication section serializes even absent block rows. Only
    reconciliation writes anchors; rejected/stale collectors cannot poison old
    unanchored ranges. Older reconciliation requests cannot replace newer facts.
    """
    key = "canonical-publication-fence-v1"
    _insert(session, AlphaWorkCursor, {"version": key, "last_pair_id": 0}, ["version"])
    session.scalar(select(AlphaWorkCursor).where(AlphaWorkCursor.version == key).with_for_update())
    if not authoritative:
        effective = {number: block_hash.lower() for number, block_hash in blocks.items()}
        for row in session.scalars(select(AlphaBlockAnchor).where(
            AlphaBlockAnchor.chain_id == 56, AlphaBlockAnchor.block_number.in_(blocks),
        ).execution_options(populate_existing=True)):
            effective[row.block_number] = row.block_hash
        return effective, []
    effective, changed = {}, []
    for number, block_hash in sorted(blocks.items()):
        _insert(session, AlphaBlockAnchor, {
            "chain_id": 56, "block_number": number, "block_hash": block_hash.lower(),
            "observed_at": observed_at,
        }, ["chain_id", "block_number"])
        row = session.scalar(select(AlphaBlockAnchor).where(
            AlphaBlockAnchor.chain_id == 56, AlphaBlockAnchor.block_number == number,
        ).with_for_update().execution_options(populate_existing=True))
        if authoritative and utc(observed_at) >= utc(row.observed_at):
            if row.block_hash != block_hash.lower():
                changed.append(number)
            row.block_hash, row.observed_at = block_hash.lower(), observed_at
        effective[number] = row.block_hash
    session.flush()
    return effective, changed

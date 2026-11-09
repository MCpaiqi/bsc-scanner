"""Lossless bounded canonical-anchor storage behind the shared publication fence."""

import hashlib
import json
import zlib
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, delete, func, select, tuple_
from sqlalchemy.exc import DBAPIError

from smms.db.models import AlphaBlockAnchor, AlphaWorkCursor, CanonicalAnchorSegment
from smms.evaluation import utc
from smms.research.maintenance import bounded_lock_wait
from smms.research.state import insert_once

BUCKET_SIZE = 512
FORMAT = "canonical-anchors-v1"
MAX_RAW_BYTES = BUCKET_SIZE * 2048 + 1024
FENCE = "canonical-publication-fence-v1"


@dataclass(frozen=True)
class Anchor:
    block_number: int
    block_hash: str
    observed_at: datetime


def _identity(chain_id, bucket_start):
    if type(chain_id) is not int or chain_id != 56 or type(bucket_start) is not int or bucket_start < 0 or bucket_start % BUCKET_SIZE:
        raise ValueError("invalid canonical archive identity")


def encode_segment(*, chain_id, bucket_start, anchors):
    _identity(chain_id, bucket_start)
    rows = sorted(anchors, key=lambda row: row.block_number)
    body = {"format_version": FORMAT, "chain_id": chain_id, "bucket_start": bucket_start,
            "bucket_end": bucket_start + BUCKET_SIZE - 1,
            "anchors": [[row.block_number, row.block_hash, utc(row.observed_at).isoformat(timespec="microseconds")] for row in rows]}
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    values = {key: body[key] for key in ("format_version", "chain_id", "bucket_start", "bucket_end")}
    values.update(row_count=len(rows), raw_bytes=len(raw), payload_sha256=hashlib.sha256(raw).hexdigest(),
                  payload=zlib.compress(raw))
    from types import SimpleNamespace

    decode_segment(SimpleNamespace(**values))
    return values


def decode_segment(segment):
    """Validate identity, byte limits, checksum and shape before trusting a fence."""
    _identity(segment.chain_id, segment.bucket_start)
    if (segment.format_version != FORMAT or segment.bucket_end != segment.bucket_start + BUCKET_SIZE - 1
            or type(segment.row_count) is not int or not 1 <= segment.row_count <= BUCKET_SIZE
            or type(segment.raw_bytes) is not int or not 0 < segment.raw_bytes <= MAX_RAW_BYTES
            or not isinstance(segment.payload, bytes) or len(segment.payload) > MAX_RAW_BYTES + 1024):
        raise ValueError("invalid canonical archive metadata")
    try:
        decoder = zlib.decompressobj()
        raw = decoder.decompress(segment.payload, MAX_RAW_BYTES + 1)
        if (len(raw) != segment.raw_bytes or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail
                or hashlib.sha256(raw).hexdigest() != segment.payload_sha256):
            raise ValueError("canonical archive checksum or byte limit mismatch")
        body = json.loads(raw)
        expected = {key: getattr(segment, key) for key in ("format_version", "chain_id", "bucket_start", "bucket_end")}
        if not isinstance(body, dict) or set(body) != {*expected, "anchors"} or any(body[key] != value for key, value in expected.items()):
            raise ValueError("canonical archive identity mismatch")
        rows = body["anchors"]
        if not isinstance(rows, list) or len(rows) != segment.row_count:
            raise ValueError("canonical archive row count mismatch")
        result = {}
        for row in rows:
            if (not isinstance(row, list) or len(row) != 3 or type(row[0]) is not int
                    or not segment.bucket_start <= row[0] <= segment.bucket_end
                    or row[0] in result or (result and row[0] <= next(reversed(result)))
                    or not isinstance(row[1], str) or not 1 <= len(row[1]) <= 128 or not isinstance(row[2], str)):
                raise ValueError("invalid canonical archive row")
            observed = datetime.fromisoformat(row[2])
            if observed.tzinfo is None:
                raise ValueError("canonical archive timestamp requires UTC offset")
            result[row[0]] = Anchor(row[0], row[1], utc(observed))
        return result
    except (zlib.error, TypeError, KeyError, OverflowError, UnicodeError, RecursionError) as exc:
        raise ValueError("invalid canonical archive payload") from exc


def lock_fence(session):
    insert_once(session, AlphaWorkCursor, {"version": FENCE, "last_pair_id": 0}, ["version"])
    session.scalar(select(AlphaWorkCursor).where(AlphaWorkCursor.version == FENCE).with_for_update())


def _segments(session, buckets):
    buckets = sorted(set(buckets))
    for offset in range(0, len(buckets), 16):
        latest = select(CanonicalAnchorSegment.bucket_start, func.max(CanonicalAnchorSegment.revision).label("revision")).where(
            CanonicalAnchorSegment.chain_id == 56, CanonicalAnchorSegment.bucket_start.in_(buckets[offset:offset + 16]),
        ).group_by(CanonicalAnchorSegment.bucket_start).subquery()
        yield from session.scalars(select(CanonicalAnchorSegment).join(latest, and_(
            CanonicalAnchorSegment.bucket_start == latest.c.bucket_start,
            CanonicalAnchorSegment.revision == latest.c.revision,
        )).where(CanonicalAnchorSegment.chain_id == 56).execution_options(populate_existing=True))


def _load(session, numbers):
    numbers = sorted(set(numbers))
    result, hot = {}, {}
    wanted = set(numbers)
    for segment in _segments(session, (n // BUCKET_SIZE * BUCKET_SIZE for n in numbers)):
        result.update({n: row for n, row in decode_segment(segment).items() if n in wanted})
    for offset in range(0, len(numbers), 200):
        for row in session.scalars(select(AlphaBlockAnchor).where(AlphaBlockAnchor.chain_id == 56,
            AlphaBlockAnchor.block_number.in_(numbers[offset:offset + 200])).execution_options(populate_existing=True)):
            hot[row.block_number] = row
            if row.block_number not in result or utc(row.observed_at) >= result[row.block_number].observed_at:
                result[row.block_number] = Anchor(row.block_number, row.block_hash, utc(row.observed_at))
    return result, hot


def anchor_blocks(session, blocks, *, observed_at, authoritative):
    lock_fence(session)
    current, hot = _load(session, blocks)
    effective = {n: row.block_hash for n, row in current.items()}
    changed, pending = [], []
    for number, block_hash in sorted(blocks.items()):
        block_hash = block_hash.lower()
        previous = current.get(number)
        if authoritative and (previous is None or utc(observed_at) >= previous.observed_at):
            if previous is not None and previous.block_hash != block_hash:
                changed.append(number)
            if number in hot:
                hot[number].block_hash, hot[number].observed_at = block_hash, observed_at
            else:
                pending.append({"chain_id": 56, "block_number": number, "block_hash": block_hash, "observed_at": observed_at})
            effective[number] = block_hash
        elif previous is None:
            effective[number] = block_hash
    for offset in range(0, len(pending), 200):
        insert_once(session, AlphaBlockAnchor, pending[offset:offset + 200], ["chain_id", "block_number"])
    session.flush()
    return effective, changed


def archive_anchors(session, *, keep_recent_blocks=4096, now):
    """Compact one complete old bucket atomically; caller owns source commit."""
    if type(keep_recent_blocks) is not int or keep_recent_blocks < BUCKET_SIZE:
        raise ValueError("retain at least512 recent anchor blocks")
    empty = {"state": "IDLE", "rows_archived": 0, "segments_written": 0, "bucket_start": None}
    # SQLite legacy SAVEPOINT mode needs an outer write before a nested rollback.
    if session.get_bind().dialect.name == "sqlite":
        lock_fence(session)
    try:
        with session.begin_nested(), bounded_lock_wait(session):
            lock_fence(session)
            head = session.scalar(select(func.max(AlphaBlockAnchor.block_number)).where(AlphaBlockAnchor.chain_id == 56))
            boundary = ((head - keep_recent_blocks + 1) // BUCKET_SIZE * BUCKET_SIZE) if head is not None else 0
            first = session.scalar(select(AlphaBlockAnchor.block_number).where(
                AlphaBlockAnchor.chain_id == 56, AlphaBlockAnchor.block_number < boundary,
            ).order_by(AlphaBlockAnchor.block_number).limit(1))
            if first is None:
                return empty
            start = first // BUCKET_SIZE * BUCKET_SIZE
            hot = session.scalars(select(AlphaBlockAnchor).where(AlphaBlockAnchor.chain_id == 56,
                AlphaBlockAnchor.block_number >= start, AlphaBlockAnchor.block_number < start + BUCKET_SIZE)
                .order_by(AlphaBlockAnchor.block_number).execution_options(populate_existing=True)).all()
            previous = next(_segments(session, [start]), None)
            merged = decode_segment(previous) if previous is not None else {}
            before = dict(merged)
            for row in hot:
                if row.block_number not in merged or utc(row.observed_at) >= merged[row.block_number].observed_at:
                    merged[row.block_number] = Anchor(row.block_number, row.block_hash, utc(row.observed_at))
            written = int(previous is None or before != merged)
            if written:
                session.add(CanonicalAnchorSegment(**encode_segment(chain_id=56, bucket_start=start, anchors=merged.values()),
                    revision=previous.revision + 1 if previous is not None else 0, created_at=now))
                session.flush()
            removed = 0
            for offset in range(0, len(hot), 200):
                rows = hot[offset:offset + 200]
                removed += session.execute(delete(AlphaBlockAnchor).where(AlphaBlockAnchor.chain_id == 56,
                    tuple_(AlphaBlockAnchor.block_number, AlphaBlockAnchor.block_hash, AlphaBlockAnchor.observed_at).in_(
                        [(row.block_number, row.block_hash, row.observed_at) for row in rows]))
                    .execution_options(synchronize_session=False)).rowcount
            if removed != len(hot):
                raise ValueError("canonical archive source changed during copy")
            for row in hot:
                session.expunge(row)
            return {"state": "ARCHIVED", "rows_archived": removed, "segments_written": written, "bucket_start": start}
    except DBAPIError as exc:
        if getattr(exc.orig, "pgcode", None) == "55P03" or getattr(exc.orig, "sqlstate", None) == "55P03":
            return {**empty, "state": "BUSY"}
        raise

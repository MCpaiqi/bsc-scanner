"""Lossless bounded canonical-anchor compaction retains old-fork fencing."""

import hashlib
import json
import zlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import event, func, select
from test_research_integrity import research_session as _research_session

from smms.alpha.work import anchor_blocks
from smms.db.models import AlphaBlockAnchor

research_session = _research_session
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def anchors(session, count=1536):
    blocks = {n: hashlib.sha256(str(n).encode()).hexdigest() for n in range(count)}
    anchor_blocks(session, blocks, observed_at=T0, authoritative=True)
    session.commit()
    return blocks


def test_codec_exact_roundtrip_and_bounded_payload():
    from smms.alpha.anchors import Anchor, decode_segment, encode_segment

    rows = [Anchor(n, f"hash-{n}", T0 + timedelta(microseconds=n)) for n in (0, 13, 511)]
    encoded = encode_segment(chain_id=56, bucket_start=0, anchors=rows)
    assert encoded["row_count"] == 3 and encoded["bucket_end"] == 511
    decoded = decode_segment(SimpleNamespace(**encoded))
    assert list(decoded.values()) == rows
    assert encoded["payload_sha256"] == hashlib.sha256(zlib.decompress(encoded["payload"])).hexdigest()
    with pytest.raises(ValueError):
        encode_segment(chain_id=56, bucket_start=0, anchors=[replace(rows[0], block_number=512)])


@pytest.mark.parametrize("corruption", ["bytes", "digest", "duplicate", "timestamp", "bucket", "trailing", "bomb", "depth"])
def test_corrupt_or_oversized_archive_fails_closed(corruption):
    from smms.alpha.anchors import MAX_RAW_BYTES, Anchor, decode_segment, encode_segment

    encoded = encode_segment(chain_id=56, bucket_start=0, anchors=[Anchor(5, "hash", T0)])
    raw = zlib.decompress(encoded["payload"])
    body = json.loads(raw)
    if corruption == "bytes":
        encoded["payload"] = b"broken"
    elif corruption == "digest":
        encoded["payload_sha256"] = "0" * 64
    elif corruption in {"duplicate", "timestamp", "bucket"}:
        if corruption == "duplicate":
            body["anchors"].append(body["anchors"][0])
            encoded["row_count"] = 2
        elif corruption == "timestamp":
            body["anchors"][0][2] = "2026-01-01T00:00:00"
        else:
            body["bucket_start"] = 512
        raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode()
        encoded.update(payload=zlib.compress(raw), raw_bytes=len(raw), payload_sha256=hashlib.sha256(raw).hexdigest())
    elif corruption == "trailing":
        encoded["payload"] += b"trailing"
    elif corruption == "bomb":
        raw = b"x" * (MAX_RAW_BYTES + 1)
        encoded.update(payload=zlib.compress(raw), raw_bytes=MAX_RAW_BYTES, payload_sha256=hashlib.sha256(raw).hexdigest())
    else:
        raw = b"[" * 2000 + b"0" + b"]" * 2000
        encoded.update(payload=zlib.compress(raw), raw_bytes=len(raw), payload_sha256=hashlib.sha256(raw).hexdigest())
    with pytest.raises(ValueError):
        decode_segment(SimpleNamespace(**encoded))


def test_json_recursion_failure_is_a_retryable_validation_error(monkeypatch):
    from smms.alpha.anchors import Anchor, decode_segment, encode_segment

    encoded = encode_segment(chain_id=56, bucket_start=0, anchors=[Anchor(5, "hash", T0)])
    raw = b"[" * 2000 + b"0" + b"]" * 2000
    encoded.update(payload=zlib.compress(raw), raw_bytes=len(raw), payload_sha256=hashlib.sha256(raw).hexdigest())
    def exhausted(_value):
        raise RecursionError("JSON decoder nesting limit")
    monkeypatch.setattr(json, "loads", exhausted)
    with pytest.raises(ValueError):
        decode_segment(SimpleNamespace(**encoded))


def test_anchor_batch_avoids_per_height_sql(research_session):
    statements = []
    def observe(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)
    event.listen(research_session.get_bind(), "before_cursor_execute", observe)
    try:
        blocks = {n: f"hash-{n}" for n in range(600)}
        assert anchor_blocks(research_session, blocks, observed_at=T0, authoritative=True) == (blocks, [])
        assert len(statements) <= 15
    finally:
        event.remove(research_session.get_bind(), "before_cursor_execute", observe)


def test_archive_drains_complete_old_buckets_and_retains_fence(research_session):
    from smms.alpha.anchors import archive_anchors
    from smms.db.models import CanonicalAnchorSegment

    blocks = anchors(research_session)
    for expected in (0, 512):
        report = archive_anchors(research_session, keep_recent_blocks=512, now=T0)
        assert report["rows_archived"] == 512 and report["bucket_start"] == expected
        research_session.commit()
    assert archive_anchors(research_session, keep_recent_blocks=512, now=T0)["rows_archived"] == 0
    assert research_session.scalar(select(func.count()).select_from(AlphaBlockAnchor)) == 512
    assert research_session.scalar(select(func.count()).select_from(CanonicalAnchorSegment)) == 2
    expected = {1: blocks[1], 600: blocks[600], 1535: blocks[1535], 9999: "unanchored"}
    effective, changed = anchor_blocks(research_session, {1: "stale", 600: "stale", 1535: "stale", 9999: "unanchored"},
                                      observed_at=T0, authoritative=False)
    assert effective == expected and changed == []


def test_archived_correction_keeps_old_revision_and_rejects_older_observation(research_session):
    from smms.alpha.anchors import archive_anchors, decode_segment
    from smms.db.models import CanonicalAnchorSegment

    blocks = anchors(research_session)
    archive_anchors(research_session, keep_recent_blocks=512, now=T0)
    research_session.commit()
    effective, changed = anchor_blocks(research_session, {1: "new-fork"}, observed_at=T0 + timedelta(seconds=20), authoritative=True)
    assert effective == {1: "new-fork"} and changed == [1]
    assert anchor_blocks(research_session, {1: "late-old"}, observed_at=T0 + timedelta(seconds=10), authoritative=True) == ({1: "new-fork"}, [])
    archive_anchors(research_session, keep_recent_blocks=512, now=T0 + timedelta(seconds=21))
    research_session.commit()
    revisions = research_session.scalars(select(CanonicalAnchorSegment).where(
        CanonicalAnchorSegment.bucket_start == 0).order_by(CanonicalAnchorSegment.revision)).all()
    assert len(revisions) == 2
    assert decode_segment(revisions[0])[1].block_hash == blocks[1]
    assert decode_segment(revisions[1])[1].block_hash == "new-fork"
    assert anchor_blocks(research_session, {1: "late-old"}, observed_at=T0 + timedelta(seconds=15), authoritative=True) == ({1: "new-fork"}, [])


def test_compaction_rollback_cannot_lose_hot_or_cold_evidence(research_session):
    from smms.alpha.anchors import archive_anchors
    from smms.db.models import CanonicalAnchorSegment

    blocks = anchors(research_session)
    archive_anchors(research_session, keep_recent_blocks=512, now=T0)
    research_session.rollback()
    assert research_session.scalar(select(func.count()).select_from(AlphaBlockAnchor)) == len(blocks)
    assert research_session.scalar(select(func.count()).select_from(CanonicalAnchorSegment)) == 0


@pytest.mark.parametrize("mutation", ["edit", "delete"])
def test_archive_revisions_are_immutable(research_session, mutation):
    from smms.alpha.anchors import archive_anchors
    from smms.db.models import CanonicalAnchorSegment

    anchors(research_session)
    archive_anchors(research_session, keep_recent_blocks=512, now=T0)
    research_session.commit()
    segment = research_session.scalar(select(CanonicalAnchorSegment))
    if mutation == "edit":
        segment.payload = b"discarded history"
    else:
        research_session.delete(segment)
    with pytest.raises(ValueError, match="immutable"):
        research_session.flush()


def test_sparse_archive_never_invents_missing_heights(research_session):
    from smms.alpha.anchors import archive_anchors, decode_segment
    from smms.db.models import CanonicalAnchorSegment

    anchor_blocks(research_session, {1: "one", 513: "middle", 2000: "head"}, observed_at=T0, authoritative=True)
    research_session.commit()
    assert archive_anchors(research_session, keep_recent_blocks=512, now=T0)["rows_archived"] == 1
    research_session.commit()
    segment = research_session.scalar(select(CanonicalAnchorSegment))
    assert set(decode_segment(segment)) == {1}
    assert anchor_blocks(research_session, {1: "old", 2: "never-observed"}, observed_at=T0, authoritative=False)[0] == {
        1: "one", 2: "never-observed"}


def test_corrupt_stored_archive_is_not_treated_as_missing_evidence(research_session):
    from sqlalchemy import update

    from smms.alpha.anchors import archive_anchors
    from smms.db.models import CanonicalAnchorSegment

    anchors(research_session)
    archive_anchors(research_session, keep_recent_blocks=512, now=T0)
    research_session.commit()
    research_session.execute(update(CanonicalAnchorSegment.__table__).values(payload=b"simulated storage corruption"))
    research_session.commit()
    with pytest.raises(ValueError):
        anchor_blocks(research_session, {1: "old-fork"}, observed_at=T0, authoritative=False)


def test_archive_skips_busy_canonical_publication_fence(research_session):
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    from smms.alpha.anchors import archive_anchors

    if research_session.get_bind().dialect.name != "postgresql":
        pytest.skip("independent PostgreSQL canonical locks required")
    blocks = anchors(research_session)
    with Session(research_session.get_bind()) as owner, Session(research_session.get_bind()) as other:
        anchor_blocks(owner, {}, observed_at=T0, authoritative=False)
        other.execute(text("SET LOCAL statement_timeout='2s'"))
        assert archive_anchors(other, keep_recent_blocks=512, now=T0)["state"] == "BUSY"
        assert other.scalar(select(func.count()).select_from(AlphaBlockAnchor)) == len(blocks)
        other.commit()


def test_two_archivers_preserve_one_revision_per_bucket(research_session):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from sqlalchemy.orm import Session

    from smms.alpha.anchors import archive_anchors
    from smms.db.models import CanonicalAnchorSegment

    if research_session.get_bind().dialect.name != "postgresql":
        pytest.skip("independent PostgreSQL archive writers required")
    anchors(research_session)
    barrier = Barrier(2)
    def write():
        with Session(research_session.get_bind()) as session:
            barrier.wait(timeout=5)
            result = archive_anchors(session, keep_recent_blocks=512, now=T0)
            session.commit()
            return result
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [future.result(timeout=10) for future in [executor.submit(write), executor.submit(write)]]
    if sum(result["rows_archived"] for result in results) < 1024:
        # A busy writer may yield under its250ms deadline and drain next cycle.
        archive_anchors(research_session, keep_recent_blocks=512, now=T0)
        research_session.commit()
    assert list(research_session.scalars(select(CanonicalAnchorSegment.revision))) == [0, 0]


def test_archive_copy_guard_preserves_a_concurrent_uncoordinated_correction(research_session):
    from sqlalchemy import update
    from sqlalchemy.orm import Session

    from smms.alpha.anchors import archive_anchors
    from smms.db.models import CanonicalAnchorSegment

    if research_session.get_bind().dialect.name != "postgresql":
        pytest.skip("independent correction requires PostgreSQL")
    anchors(research_session)
    changed = []
    def correct(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("DELETE") and "alpha_block_anchors" in statement and not changed:
            changed.append(True)
            with Session(research_session.get_bind()) as external:
                external.execute(update(AlphaBlockAnchor).where(AlphaBlockAnchor.block_number == 1)
                                 .values(block_hash="external-correction"))
                external.commit()
    event.listen(research_session.get_bind(), "before_cursor_execute", correct)
    try:
        with pytest.raises(ValueError, match="source changed"):
            archive_anchors(research_session, keep_recent_blocks=512, now=T0)
    finally:
        event.remove(research_session.get_bind(), "before_cursor_execute", correct)
    research_session.rollback()
    assert research_session.scalar(select(func.count()).select_from(CanonicalAnchorSegment)) == 0
    assert research_session.scalar(select(func.count()).select_from(AlphaBlockAnchor)) == 1536
    assert research_session.get(AlphaBlockAnchor, (56, 1)).block_hash == "external-correction"

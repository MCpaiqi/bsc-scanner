"""Evidence and recovery regressions for live Alpha research."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from test_research_integrity import research_session as _research_session

from smms.alpha.live import _features, process_live_alpha, score_features
from smms.alpha.work import SWAP_TOPIC, coverage_proof, invalidate_coverage, register_pair
from smms.db.models import (
    AlphaCoverageJob,
    AlphaCoverageReceipt,
    AlphaSnapshot,
    Pair,
    Token,
    TradeEvent,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)
TOKEN, QUOTE, PAIR, FACTORY = (f"0x{n:040x}" for n in (101, 102, 103, 104))
research_session = _research_session


def pair(session):
    session.add_all([
        Token(chain="bsc", address=TOKEN, decimals=18),
        Token(chain="bsc", address=QUOTE, decimals=18),
    ])
    row = Pair(chain_id=56, address=PAIR, token0_address=TOKEN, token1_address=QUOTE,
               quote_token=QUOTE, factory_address=FACTORY, creation_timestamp=T0,
               creation_block=100, creation_block_hash="0xblock100", dex="pancakeswap")
    session.add(row)
    session.flush()
    return row


def trade(session, seconds, *, price="2", quote="2", wallet="buyer", tx=None):
    price, quote = Decimal(price), Decimal(quote)
    row = TradeEvent(chain="bsc", token_address=TOKEN, pair_address=PAIR,
                     base_token_address=QUOTE, trader_address=wallet,
                     tx_hash=tx or f"trade-{seconds}", block_hash=f"block-{seconds}",
                     block_number=100 + seconds, timestamp=T0 + timedelta(seconds=seconds),
                     side="BUY", quote_amount=quote, token_amount=quote / price,
                     price_quote=price, source="fixture")
    session.add(row)
    session.flush()
    return row


def test_missing_coverage_cannot_become_a_mature_negative(research_session):
    pair(research_session)
    trade(research_session, 10)
    process_live_alpha(research_session, now=T0 + timedelta(seconds=30))
    trade(research_session, 40)

    process_live_alpha(research_session, now=T0 + timedelta(seconds=4000))

    row = research_session.scalar(select(AlphaSnapshot).order_by(AlphaSnapshot.id))
    assert row.outcome_json.get("reached_10x") is None
    assert row.outcome_json.get("tracking_complete") is not True


def test_pending_alpha_is_still_evaluated_after_two_days(research_session):
    pair(research_session)
    trade(research_session, 10)
    process_live_alpha(research_session, now=T0 + timedelta(seconds=30))
    later = T0 + timedelta(days=2)

    process_live_alpha(research_session, now=later)

    row = research_session.scalar(select(AlphaSnapshot).order_by(AlphaSnapshot.id))
    assert row.outcome_json.get("evaluated_at") == later.isoformat()


def test_production_features_can_reach_the_high_score_band():
    events = []
    for index in range(1000):
        early = index < 50
        events.append(TradeEvent(
            trader_address=f"wallet{index}", side="BUY",
            quote_amount=Decimal("0.1") if early else Decimal(10),
            timestamp=T0 + timedelta(seconds=1 if early else 29),
        ))
    result = score_features(_features(events, T0 + timedelta(seconds=30)))

    assert 70 <= result["alpha_score"] <= 100
    assert result["risk_status"] == "UNKNOWN"


def test_zero_quote_observations_do_not_crash_feature_extraction():
    features = _features([TradeEvent(
        trader_address="zero-wallet", side="BUY", quote_amount=Decimal(0), timestamp=T0,
    )], T0 + timedelta(seconds=30))

    assert features["top5_buyer_share"] is None
    assert score_features(features)["risk_status"] == "UNKNOWN"


def receipt(session, row, *, start=100, end=400, start_seconds=0, end_seconds=4000,
            parent="before-creation", start_hash="0xblock100", end_hash="last"):
    register_pair(session, row)
    job = session.scalar(select(AlphaCoverageJob).where(AlphaCoverageJob.pair_id == row.id))
    item = AlphaCoverageReceipt(
        job_id=job.id, generation=job.generation, start_block=start, end_block=end,
        start_hash=start_hash, end_hash=end_hash, start_parent_hash=parent,
        start_at=T0 + timedelta(seconds=start_seconds), end_at=T0 + timedelta(seconds=end_seconds),
        raw_count=0, decoded_count=0, completed_at=T0 + timedelta(seconds=end_seconds),
        query_json={"chain_id": 56, "pair": PAIR, "topic": SWAP_TOPIC,
                    "protocol": "pancakeswap", "confirmed": True},
    )
    session.add(item)
    session.flush()
    return item


def test_zero_log_scan_proves_absence_and_late_work_recovers(research_session):
    p = pair(research_session)
    trade(research_session, 10)
    process_live_alpha(research_session, now=T0 + timedelta(seconds=30))
    proof = receipt(research_session, p)
    process_live_alpha(research_session, now=T0 + timedelta(days=2))
    rows = research_session.scalars(select(AlphaSnapshot)).all()
    assert len(rows) == 4
    assert all(r.task_status == "COMPLETE" for r in rows)
    assert all(r.outcome_json["reached_10x"] is False for r in rows)
    assert all(r.outcome_json["future_coverage"]["receipt_ids"] == [proof.id] for r in rows)


@pytest.mark.parametrize("defect", ["gap", "parent", "fork", "topic", "pair", "generation", "decode", "future", "removed"])
def test_invalid_receipts_never_prove_coverage(research_session, defect):
    p = pair(research_session)
    first = receipt(research_session, p, end=199, end_seconds=100, end_hash="boundary")
    second = receipt(research_session, p, start=200, start_seconds=101,
                     start_hash="next", parent="boundary")
    if defect == "gap":
        second.start_block += 1
    elif defect == "parent":
        second.start_parent_hash = "other-fork"
    elif defect == "fork":
        first.start_hash = "other-creation"
    elif defect in {"topic", "pair"}:
        second.query_json = {**second.query_json, defect: "wrong"}
    elif defect == "generation":
        second.generation += 1
    elif defect == "decode":
        second.raw_count = 1
    elif defect == "future":
        second.completed_at = T0 + timedelta(days=10)
    else:
        p.removed = True
    research_session.flush()
    assert coverage_proof(research_session, p, start=T0, end=T0 + timedelta(hours=1),
                          as_of=T0 + timedelta(days=2))["status"] == "MISSING"


def test_observed_positive_is_not_a_partial_negative(research_session):
    p = pair(research_session)
    trade(research_session, 10)
    receipt(research_session, p, end_seconds=30)
    process_live_alpha(research_session, now=T0 + timedelta(seconds=30))
    trade(research_session, 40, price="6")
    process_live_alpha(research_session, now=T0 + timedelta(seconds=100))
    row = research_session.scalar(select(AlphaSnapshot).where(AlphaSnapshot.observation_seconds == 30))
    assert row.outcome_json["reached_3x"] is True
    assert row.outcome_json["reached_10x"] is None
    assert row.task_status == "TRACKING"


def test_reorg_retains_but_excludes_scored_inputs(research_session):
    p = pair(research_session)
    trade(research_session, 10)
    receipt(research_session, p)
    process_live_alpha(research_session, now=T0 + timedelta(days=2))
    old = research_session.scalar(select(AlphaSnapshot))
    score = old.alpha_score
    assert invalidate_coverage(research_session, from_block=300, now=T0 + timedelta(days=3)) == 1
    assert old.task_status == "INVALIDATED"
    assert old.alpha_score == score
    assert old.outcome_json["reached_10x"] is None
    assert coverage_proof(research_session, p, start=T0, end=T0 + timedelta(hours=1),
                          as_of=T0 + timedelta(days=3))["status"] == "MISSING"


def test_notional_policy_is_frozen_before_data_arrives(research_session):
    p = pair(research_session)
    trade(research_session, 10, quote="0.02")
    process_live_alpha(research_session, now=T0 + timedelta(seconds=30), minimum_quote=Decimal("0.01"))
    receipt(research_session, p)
    process_live_alpha(research_session, now=T0 + timedelta(days=2), minimum_quote=Decimal(100))
    first = research_session.scalar(select(AlphaSnapshot).where(AlphaSnapshot.observation_seconds == 30))
    assert first.task_status == "COMPLETE"
    assert first.outcome_json["minimum_quote"] == "0.01"


def test_entry_in_the_creation_block_is_part_of_the_observation(research_session):
    p = pair(research_session)
    trade(research_session, 0)
    receipt(research_session, p)
    process_live_alpha(research_session, now=T0 + timedelta(days=2))
    first = research_session.scalar(select(AlphaSnapshot).where(AlphaSnapshot.observation_seconds == 30))
    assert first.alpha_score is not None and first.entry_vwap == 2


def test_collector_rejects_log_scope_before_persistence():
    from smms.alpha.collector import validate_scan
    from smms.chains.base import Block, ChainLog

    block = Block(number=100, block_hash="0xblock100", timestamp=T0, raw={"parentHash": "prev"})
    wrong = ChainLog(address=TOKEN, topics=(SWAP_TOPIC,), data="0x", block_number=100,
                     transaction_hash="tx", log_index=0, block_hash=block.block_hash)
    with pytest.raises(ValueError, match="scope"):
        validate_scan([wrong], pair_address=PAIR, start=100, end=100, headers={100: block})


def test_rates_use_one_complete_policy_matched_cohort_per_pair(research_session):
    from smms.api.radar import alpha_statistics

    p = pair(research_session)
    trade(research_session, 10)
    receipt(research_session, p)
    now = T0 + timedelta(days=2)
    process_live_alpha(research_session, now=now)
    result = alpha_statistics(research_session, now=now, minimum_quote=Decimal("0.01"))
    assert result["scored_tokens"] == 1 and result["matured_1h"] == 1
    assert result["overall"]["10"] == {"hits": 0, "denominator": 1, "rate": 0}
    changed_policy = alpha_statistics(research_session, now=now, minimum_quote=Decimal(1))
    assert changed_policy["matured_1h"] == 0 and changed_policy["overall"]["10"]["rate"] is None
    invalidate_coverage(research_session, from_block=200, now=now)
    research_session.flush()
    invalidated = alpha_statistics(research_session, now=now, minimum_quote=Decimal("0.01"))
    assert invalidated["matured_1h"] == invalidated["scored_tokens"] == 0


def test_latest_candidate_cannot_join_an_invalidated_snapshot_at_the_same_time(research_session):
    from smms.api.radar import _fresh_alpha_latest_rows

    p = pair(research_session)
    trade(research_session, 10)
    receipt(research_session, p)
    now = T0 + timedelta(days=2)
    process_live_alpha(research_session, now=now)
    rows = research_session.scalars(select(AlphaSnapshot).order_by(AlphaSnapshot.observation_seconds)).all()
    for row in rows[1:]:
        row.task_status = "INVALIDATED"
    rows[0].snapshot_at = rows[1].snapshot_at
    research_session.flush()
    visible = _fresh_alpha_latest_rows(research_session, now=now, fresh_seconds=200000, limit=100)
    assert [row.id for row in visible] == [rows[0].id]


def test_cyclic_repair_recovers_commit_order_inversion(research_session):
    from sqlalchemy.orm import Session

    from smms.alpha.work import repair_work

    if research_session.bind.dialect.name != "postgresql":
        pytest.skip("sequence allocation before commit requires PostgreSQL")
    pair(research_session)
    research_session.commit()
    with Session(research_session.bind) as delayed, Session(research_session.bind) as fast:
        earlier = Pair(chain_id=56, address="late-commit", token0_address=TOKEN, token1_address=QUOTE,
                       quote_token=QUOTE, creation_timestamp=T0)
        delayed.add(earlier)
        delayed.flush()
        fast.add(Pair(chain_id=56, address="early-commit", token0_address=TOKEN, token1_address=QUOTE,
                      quote_token=QUOTE, creation_timestamp=T0))
        fast.commit()
        repair_work(research_session, batch_size=1)
        repair_work(research_session, batch_size=1)
        research_session.commit()
        delayed.commit()
        for _ in range(4):
            repair_work(research_session, batch_size=1)
        assert research_session.scalar(select(AlphaCoverageJob).where(
            AlphaCoverageJob.pair_id == earlier.id,
        )) is not None


def test_older_reconciliation_cannot_replace_newer_anchor(research_session):
    from smms.alpha.work import anchor_blocks

    anchor_blocks(research_session, {100: "new"}, observed_at=T0 + timedelta(seconds=10), authoritative=True)
    effective, changed = anchor_blocks(research_session, {100: "old"}, observed_at=T0, authoritative=True)
    assert effective == {100: "new"} and changed == []

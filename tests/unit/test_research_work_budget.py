"""Bounded due work and shared point-in-time facts for research workers."""

from copy import deepcopy
from datetime import timedelta
from decimal import Decimal, localcontext

import pytest
from sqlalchemy import event, select
from test_alpha_coverage_integrity import PAIR, QUOTE, T0, TOKEN, pair, receipt, trade
from test_research_integrity import research_session as _research_session

from smms.alpha.live import process_live_alpha
from smms.alpha.policy import SCORE_VERSION, policy_id
from smms.alpha.work import invalidate_coverage
from smms.db.models import AlphaSnapshot, TradeEvent

research_session = _research_session
NOW = T0 + timedelta(seconds=4000)


def test_alpha_reads_trades_and_receipts_once_for_the_selected_batch(research_session):
    p = pair(research_session)
    receipt(research_session, p)
    entry = trade(research_session, 10)
    trade(research_session, 40, price="4")
    trade(research_session, 3599, price="6")
    excluded = [trade(research_session, 3901, price="900"), trade(research_session, 4100, price="900")]
    orphan = trade(research_session, 120, price="900")
    orphan.canonical, orphan.removed = False, True
    excluded.append(orphan)
    other = TradeEvent(chain="bsc", token_address=QUOTE, pair_address=PAIR, trader_address="quote-wallet",
        tx_hash="other-subject", timestamp=T0 + timedelta(seconds=20), side="BUY", quote_amount=2,
        token_amount=1, price_quote=900, source="fixture")
    research_session.add(other)
    research_session.flush()
    excluded.append(other)
    counts = {"trade_events": 0, "alpha_coverage_receipts": 0}
    def observe(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().lower().startswith("select"):
            for table in counts:
                if table in statement.lower():
                    counts[table] += 1
    bind = research_session.get_bind()
    event.listen(bind, "before_cursor_execute", observe)
    try:
        assert process_live_alpha(research_session, now=NOW) == 4
    finally:
        event.remove(bind, "before_cursor_execute", observe)
    rows = research_session.scalars(select(AlphaSnapshot).where(AlphaSnapshot.score_version == SCORE_VERSION)
        .order_by(AlphaSnapshot.observation_seconds)).all()
    assert all(row.task_status == "COMPLETE" for row in rows)
    assert rows[0].input_manifest["trade_ids"] == [entry.id]
    for row in rows:
        ids = row.input_manifest["trade_ids"] + row.outcome_json["evaluation_manifest"]["trade_ids"]
        assert not ({item.id for item in excluded} & set(ids))
    assert counts["trade_events"] <= 1
    assert counts["alpha_coverage_receipts"] <= 1


def test_old_recipe_is_retained_but_canonical_invalidation_covers_archived_results(research_session):
    p = pair(research_session)
    receipt(research_session, p)
    trade(research_session, 10)
    legacy = AlphaSnapshot(chain="bsc", token_address=TOKEN, pair_address=PAIR, pair_created_at=T0,
        snapshot_at=T0 + timedelta(seconds=60), age_seconds=60, observation_seconds=60,
        alpha_score=99, score_version="experimental-v3", scored_at=NOW, task_status="COMPLETE",
        coverage_generation=0, entry_vwap=2, features_json={"legacy": True},
        input_manifest={"schema_version": "alpha-inputs-v1", "score_version": "experimental-v3"},
        outcome_json={"reached_10x": False, "label_version": "alpha-shadow-v2", "tracking_complete": True})
    research_session.add(legacy)
    research_session.flush()
    original = deepcopy((legacy.alpha_score, legacy.features_json, legacy.input_manifest, legacy.outcome_json))
    process_live_alpha(research_session, now=NOW)
    assert original == (legacy.alpha_score, legacy.features_json, legacy.input_manifest, legacy.outcome_json)
    invalidate_coverage(research_session, from_block=110, now=NOW)
    assert legacy.task_status == "INVALIDATED" and legacy.alpha_score == 99
    assert legacy.outcome_json["reached_10x"] is None
    assert legacy.outcome_json["invalidated_outcome"]["reached_10x"] is False


def test_exact_notional_policy_identity_is_context_independent():
    a, b = Decimal("10000000000.0001"), Decimal("10000000000.0002")
    expected = policy_id(a)
    with localcontext() as context:
        context.prec = 8
        assert policy_id(a) == expected
        assert policy_id(a) != policy_id(b)
    assert policy_id(Decimal("0.0100")) == policy_id(Decimal(".01"))


def test_large_forward_price_batch_does_not_exceed_database_expression_limits(research_session):
    from smms.radar.forward_prices import load_usd_prices

    ranges = [(f"subject-{index}", T0, T0 + timedelta(seconds=30)) for index in range(1100)]
    assert not load_usd_prices(research_session, ranges, as_of=NOW)


def test_alpha_batched_windows_do_not_mix_pair_subjects(research_session, monkeypatch):
    from smms.alpha import inputs
    from smms.db.models import Pair

    first = pair(research_session)
    receipt(research_session, first)
    first_trade = trade(research_session, 10)
    second = Pair(chain_id=56, address="second-pair", token0_address="second-token", token1_address=QUOTE,
        quote_token=QUOTE, creation_timestamp=T0, creation_block=100, creation_block_hash="0xblock100", dex="pancakeswap")
    research_session.add(second)
    research_session.flush()
    proof = receipt(research_session, second)
    proof.query_json = {**proof.query_json, "pair": second.address}
    second_trade = trade(research_session, 10, price="8", tx="second-pair-entry")
    second_trade.pair_address, second_trade.token_address = second.address, second.token0_address
    research_session.flush()
    monkeypatch.setattr(inputs, "PREDICATES_PER_QUERY", 1)
    assert process_live_alpha(research_session, now=NOW) == 8
    for row in research_session.scalars(select(AlphaSnapshot).where(AlphaSnapshot.score_version == SCORE_VERSION)):
        expected = first_trade if row.pair_address == first.address else second_trade
        assert row.input_manifest["trade_ids"] == [expected.id]
        assert row.entry_vwap == (2 if expected is first_trade else 8)


@pytest.mark.parametrize("cold_cursor", [True, False])
def test_busy_alpha_repair_does_not_block_an_independent_due_claim(research_session, cold_cursor):
    from sqlalchemy import text
    from sqlalchemy.orm import Session
    from test_alpha_coverage_integrity import FACTORY

    from smms.alpha.collector import claim_work
    from smms.alpha.work import repair_work
    from smms.db.models import AlphaWorkCursor

    bind = research_session.get_bind()
    if bind.dialect.name != "postgresql":
        pytest.skip("requires independent PostgreSQL locks")
    p = pair(research_session)
    receipt(research_session, p)
    if not cold_cursor:
        repair_work(research_session, batch_size=1)
    research_session.commit()
    with Session(bind) as owner, Session(bind) as other:
        if cold_cursor:
            repair_work(owner, batch_size=1)
        else:
            owner.scalar(select(AlphaWorkCursor).where(AlphaWorkCursor.version == SCORE_VERSION).with_for_update())
        other.execute(text("SET LOCAL lock_timeout='250ms'"))
        other.execute(text("SET LOCAL statement_timeout='2s'"))
        claim = claim_work(other, now=NOW, factories={FACTORY})
        assert claim is not None and claim.pair_id == p.id


def test_cold_forward_repair_does_not_block_an_independent_due_evaluation(research_session):
    from sqlalchemy import text
    from sqlalchemy.orm import Session
    from test_forward_evaluation_integrity import market, observation

    from smms.radar.live_forward import _initialize_outcomes, enqueue_horizons, evaluate_due

    bind = research_session.get_bind()
    if bind.dialect.name != "postgresql":
        pytest.skip("requires independent PostgreSQL locks")
    row = observation(research_session)
    market(research_session, row, 30, "1.2")
    enqueue_horizons(research_session, row)
    research_session.commit()
    now = T0 + timedelta(seconds=30)
    with Session(bind) as owner, Session(bind) as other:
        _initialize_outcomes(owner, batch_size=1, now=now)
        other.execute(text("SET LOCAL lock_timeout='250ms'"))
        other.execute(text("SET LOCAL statement_timeout='2s'"))
        assert evaluate_due(other, at=now, batch_size=1) == 1


def test_not_due_and_completed_alpha_cohorts_do_not_reprice(research_session):
    p = pair(research_session)
    receipt(research_session, p)
    trade(research_session, 10)
    reads = []
    def observe(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().lower().startswith("select") and "trade_events" in statement.lower():
            reads.append(statement)
    bind = research_session.get_bind()
    event.listen(bind, "before_cursor_execute", observe)
    try:
        assert process_live_alpha(research_session, now=T0 + timedelta(seconds=20)) == 0
        assert reads == []
        assert process_live_alpha(research_session, now=NOW) == 4
        assert len(reads) == 1
        reads.clear()
        assert process_live_alpha(research_session, now=NOW + timedelta(days=2)) == 0
        assert reads == []
    finally:
        event.remove(bind, "before_cursor_execute", observe)

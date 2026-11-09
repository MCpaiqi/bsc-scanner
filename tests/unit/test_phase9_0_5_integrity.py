from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test_phase9_alpha_baseline import T0, TOKEN, _pair, _session, _trade

from smms.alpha.baseline import PRICE_BASIS_HUMAN, PRICE_BASIS_RAW, rolling_vwap
from smms.alpha.live import SCORE_VERSION, process_live_alpha, score_features
from smms.alpha.work import SWAP_TOPIC, register_pair
from smms.api.radar import _alpha_payload, _fresh_alpha_latest_rows
from smms.db.models import AlphaCoverageJob, AlphaCoverageReceipt, AlphaSnapshot, Base, Pair, Token


def covered_pair(session):
    _pair(session)
    session.flush()
    pair = session.scalar(select(Pair))
    pair.creation_block, pair.creation_block_hash = 100, "creation"
    session.flush()
    register_pair(session, pair)
    job = session.scalar(select(AlphaCoverageJob))
    session.add(AlphaCoverageReceipt(
        job_id=job.id, generation=0, start_block=100, end_block=199,
        start_hash="creation", start_parent_hash="parent", end_hash="end",
        start_at=T0, end_at=T0 + timedelta(seconds=30), raw_count=0, decoded_count=0,
        completed_at=T0 + timedelta(seconds=30),
        query_json={"chain_id": 56, "pair": pair.address, "topic": SWAP_TOPIC,
                    "protocol": "pancakeswap", "confirmed": True},
    ))


def raw_trade(seconds, *, tx, ratio=2, human=False, quote="2"):
    row = _trade(T0 + timedelta(seconds=seconds), tx=tx,
                  price_quote=str(ratio * 10**9) if human else None,
                  quote=quote, token=str(Decimal(quote) / (ratio * 10**9)) if human else None,
                  metadata={"amounts_raw": ["0", str(ratio * 10**18), str(10**18), "0"]})
    row.block_number = 100 + seconds
    return row


def test_unknown_risk_is_not_safe_and_version_is_v4():
    result = score_features({})
    assert SCORE_VERSION == result["score_version"] == "experimental-v4"
    assert result["risk_status"] == "UNKNOWN"
    assert result["components"]["risk_quality"] == 0
    assert "risk data unavailable" in result["warnings"]
    rapid = score_features({"rapid_fire_ratio": "0.9"})
    assert rapid["risk_status"] == "UNKNOWN"
    assert rapid["components"]["risk_quality"] == -6


@pytest.mark.parametrize("basis", [PRICE_BASIS_RAW, PRICE_BASIS_HUMAN])
def test_live_basis_entry_returns_and_future_score_invariant(basis):
    with _session() as session:
        covered_pair(session)
        session.flush()
        if basis == PRICE_BASIS_RAW:
            session.scalar(select(Token).where(Token.address == TOKEN)).decimals = None
        session.add(raw_trade(10, tx="entry", human=basis == PRICE_BASIS_HUMAN))
        session.commit()
        process_live_alpha(session, now=T0 + timedelta(seconds=30))
        row = session.scalar(select(AlphaSnapshot))
        before = deepcopy((row.alpha_score, row.features_json, row.components_json,
                           row.positive_reasons, row.negative_reasons, row.warnings))
        assert row.outcome_json["price_basis"] == basis
        assert row.entry_vwap == (Decimal(2) if basis == PRICE_BASIS_RAW else Decimal(2 * 10**9))
        # Later decimals discovery must not change a raw entry's future units.
        session.add(raw_trade(40, tx="future", ratio=4, human=True, quote="4"))
        session.commit()
        process_live_alpha(session, now=T0 + timedelta(seconds=100))
        session.flush()
        session.refresh(row)
        assert row.current_return == row.max_tick_return == row.max_realisable_return == 1
        assert row.outcome_json["price_basis"] == basis
        assert row.outcome_json["reached_2x"] is True
        assert row.outcome_json["reached_3x"] is None
        assert before == (row.alpha_score, row.features_json, row.components_json,
                          row.positive_reasons, row.negative_reasons, row.warnings)
        assert _alpha_payload(row, now=T0 + timedelta(seconds=45))["risk_status"] == "UNKNOWN"


def test_shared_vwap_never_mixes_units_and_dust_threshold_uses_human_quote():
    with _session() as session:
        _pair(session)
        session.flush()
        pair = session.scalar(select(Pair))
        events = [raw_trade(10, tx="raw"), raw_trade(20, tx="human", ratio=4, human=True, quote="4")]
        kwargs = {"start": T0, "end": T0 + timedelta(seconds=30), "min_quote_notional": Decimal("0.01"), "pair": pair}
        assert rolling_vwap(events, price_basis=PRICE_BASIS_RAW, **kwargs) == 3
        assert rolling_vwap(events, price_basis=PRICE_BASIS_HUMAN, **kwargs) == 4 * 10**9
        assert rolling_vwap([raw_trade(10, tx="dust", quote="0.000001")], price_basis=PRICE_BASIS_RAW, **kwargs) is None


def test_versions_coexist_and_current_list_excludes_high_score_v1():
    with _session() as session:
        covered_pair(session)
        session.add(raw_trade(10, tx="entry"))
        session.commit()
        process_live_alpha(session, now=T0 + timedelta(seconds=30))
        row = session.scalar(select(AlphaSnapshot))
        old = AlphaSnapshot(**{column.name: getattr(row, column.name)
                               for column in AlphaSnapshot.__table__.columns if column.name != "id"})
        old.score_version = "experimental-v1"
        old.alpha_score = 99
        session.add(old)
        session.commit()
        assert process_live_alpha(session, now=T0 + timedelta(seconds=30)) == 0
        assert len(session.scalars(select(AlphaSnapshot)).all()) == 5
        rows = _fresh_alpha_latest_rows(session, now=T0 + timedelta(seconds=40), fresh_seconds=900, limit=1)
        assert len(rows) == 1 and rows[0].score_version == SCORE_VERSION
        assert session.get(AlphaSnapshot, old.id).alpha_score == 99


def test_old_pairs_and_snapshots_are_bounded_and_outcome_horizon_is_one_hour():
    with _session() as session:
        covered_pair(session)
        session.add(raw_trade(10, tx="entry"))
        session.commit()
        process_live_alpha(session, now=T0 + timedelta(seconds=30))
        session.add_all([raw_trade(40, tx="within", ratio=4), raw_trade(3631, tx="beyond", ratio=100)])
        session.commit()
        process_live_alpha(session, now=T0 + timedelta(seconds=3700))
        row = session.scalar(select(AlphaSnapshot))
        assert row.max_tick_return == 1
        assert len(session.scalars(select(AlphaSnapshot)).all()) == 4
        before = deepcopy(row.outcome_json)
        process_live_alpha(session, now=T0 + timedelta(days=2))
        assert row.outcome_json["evaluated_at"] != before["evaluated_at"]
        assert row.outcome_json["tracking_complete"] is False


def test_raw_entry_precision_survives_legacy_numeric_column():
    with _session() as session:
        covered_pair(session)
        event = raw_trade(10, tx="tiny")
        event.metadata_json = {"amounts_raw": ["0", "1", str(10**30), "0"]}
        session.add(event)
        session.commit()
        process_live_alpha(session, now=T0 + timedelta(seconds=30))
        session.commit()
        row = session.scalar(select(AlphaSnapshot))
        assert Decimal(_alpha_payload(row, now=T0)["outcome"]["entry_vwap"]) == Decimal("1e-30")
        future = raw_trade(40, tx="future")
        future.metadata_json = {"amounts_raw": ["0", "2", str(10**30), "0"]}
        session.add(future)
        session.commit()
        process_live_alpha(session, now=T0 + timedelta(seconds=100))
        assert row.current_return == row.max_realisable_return == 1


@pytest.mark.asyncio
async def test_stats_and_detail_default_to_current_version(monkeypatch):
    from smms.api import radar

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        for version, token, score in [("experimental-v1", "old", 99), (SCORE_VERSION, "new", 10)]:
            session.add(AlphaSnapshot(
                chain="bsc", token_address=token, pair_address=f"{token}-pair",
                pair_created_at=T0, snapshot_at=T0 + timedelta(seconds=30),
                observation_seconds=30, age_seconds=30, scored_at=T0,
                score_version=version, alpha_score=score, task_status="TRACKING", outcome_json={},
            ))
        await session.commit()

        @asynccontextmanager
        async def scope(_settings):
            yield session

        monkeypatch.setattr(radar, "session_scope", scope)
        result = await radar.alpha_stats()
        assert result["score_version"] == SCORE_VERSION
        assert result["scored_tokens"] == 1
        assert result["high_score_tokens"] == 0
        detail = await radar.alpha_candidate("new")
        assert detail["latest"]["score_version"] == SCORE_VERSION
        with pytest.raises(radar.HTTPException):
            await radar.alpha_candidate("old")
    await engine.dispose()

"""Candidate selection is complete, bounded and faithful to canonical snapshots."""

from datetime import timedelta

import pytest
from sqlalchemy import select
from test_derived_snapshot_integrity import T0
from test_research_integrity import research_session as _research_session

from smms.db.models import RadarCandidate, Token
from smms.radar.config import RadarConfig
from smms.radar.domain import CandidateStatus
from smms.radar.views import candidates

research_session = _research_session
AT = T0 + timedelta(hours=1)


def seed_rows(session, values):
    tokens = [Token(chain="bsc", address=f"0x{10000 + i:040x}", symbol=f"T{i}", name=f"Token {i}")
              for i in range(len(values))]
    session.add_all(tokens)
    session.flush()
    rows = [RadarCandidate(token_id=token.id, discovered_at=AT - timedelta(seconds=len(values) - i),
                           latest_seen_at=AT, discovery_source="fixture", discovery_reason="fixture",
                           _status=CandidateStatus.WATCH, priority_score=1, priority_json={"priority_score": 1},
                           features_json={"liquidity": value, "risk": "LOW"}, data_status="OBSERVED",
                           created_at=AT, updated_at=AT) for i, (token, value) in enumerate(zip(tokens, values, strict=True))]
    session.add_all(rows)
    session.flush()
    return tokens, rows


def page(session, **kwargs):
    return candidates(session, RadarConfig(), 56, at=AT, chain="bsc", **kwargs)


def test_age_order_is_newest_discovery_first_and_uses_one_cutoff(research_session):
    tokens, _rows = seed_rows(research_session, [1, 2, 3])
    result = page(research_session, sort="age")
    assert [row["token_address"] for row in result["candidates"]] == [token.address for token in reversed(tokens)]
    assert all(row["freshness"] == "FRESH" for row in result["candidates"])
    assert result["as_of"] == AT


def test_zero_and_scientific_values_sort_before_unknowns_and_nonfinite_filter_out(research_session):
    tokens, _rows = seed_rows(research_session, ["UNKNOWN", "0", "1e3", "3.1", None, "NaN", "Infinity", "-1"])
    result = page(research_session, sort="liquidity")
    assert [row["token_address"] for row in result["candidates"][:3]] == [tokens[i].address for i in [2, 3, 1]]
    filtered = page(research_session, liquidity=0)
    assert filtered["total"] == 3


def test_global_search_finds_a_candidate_beyond_the_old_first_500(research_session):
    tokens, _rows = seed_rows(research_session, [1] * 505)
    tokens[-1].name = "Needle beyond first page"
    research_session.flush()
    result = page(research_session, q="needle", limit=10)
    assert result["total"] == result["count"] == 1
    assert result["candidates"][0]["token_address"] == tokens[-1].address


def test_only_the_selected_page_is_hydrated_and_counts_are_global(research_session, monkeypatch):
    from smms.radar import views

    seed_rows(research_session, [1] * 505)
    hydrated = []
    original = views.candidate_view

    def observe(session, candidate, **kwargs):
        hydrated.append(candidate.id)
        return original(session, candidate, **kwargs)

    monkeypatch.setattr(views, "candidate_view", observe)
    result = page(research_session, limit=50, offset=500)
    assert result["total"] == 505 and result["count"] == 5
    assert len(hydrated) == 5
    assert result["has_more"] is False


def test_unknown_values_sort_last_even_in_ascending_order(research_session):
    tokens, _rows = seed_rows(research_session, [None, 0, 100])
    result = page(research_session, sort="liquidity", direction="asc")
    assert [row["token_address"] for row in result["candidates"]] == [tokens[i].address for i in [1, 2, 0]]


def test_canonical_epoch_change_during_hydration_rejects_the_read(research_session, monkeypatch):
    from smms.radar import views
    from smms.research.state import invalidate_derived

    seed_rows(research_session, [1])
    original = views.candidate_view

    def reconcile(session, candidate, **kwargs):
        result = original(session, candidate, **kwargs)
        invalidate_derived(session, reason="TEST_BROWSER_REORG", cause={})
        return result

    monkeypatch.setattr(views, "candidate_view", reconcile)
    with pytest.raises(ValueError, match="changed"):
        page(research_session)
    assert research_session.scalar(select(Token.address))


def add_projection(session, row, **kwargs):
    from smms.db.models import RadarFeatureObservation
    token = session.get(Token, row.token_id)
    projection = RadarFeatureObservation(chain="bsc", token_address=token.address, candidate_id=row.id,
        as_of=AT, config_hash="browser", features_json=row.features_json, priority_json=row.priority_json,
        context_json={}, data_status="OBSERVED")
    for key, value in kwargs.items():
        setattr(projection, key, value)
    session.add(projection)
    session.flush()
    row.projection_id = projection.id
    session.flush()
    return projection


def test_literal_search_includes_pair_and_platform_without_sql_wildcards(research_session):
    from smms.db.models import Pair
    tokens, _ = seed_rows(research_session, [1, 1])
    tokens[0].name = "literal_100%"
    tokens[1].name = "literalX100many"
    research_session.add(Pair(chain_id=56, address="pair-only-needle", token0_address=tokens[0].address,
                             token1_address="quote", dex="pancakeswap", creation_timestamp=T0))
    research_session.flush()
    for query in ("literal_100%", "pair-only-needle", "PancakeSwap V2"):
        result = page(research_session, q=query)
        assert result["total"] == 1
        assert result["candidates"][0]["token_address"] == tokens[0].address


def test_corrupt_priority_and_wrong_subject_snapshot_stay_unknown(research_session):
    _, rows = seed_rows(research_session, [1, 1])
    add_projection(research_session, rows[0], priority_json={"priority_score": "UNKNOWN"})
    add_projection(research_session, rows[1], token_address="other-subject")
    result = page(research_session)
    mapped = {row["token_address"]: row for row in result["candidates"]}
    assert next(iter(mapped.values()))["priority_score"] == "UNKNOWN"
    wrong = result["candidates"][1]
    assert wrong["data_status"] == "REBUILDING" and wrong["features"]["liquidity"] == "UNKNOWN"


def test_global_facets_filters_and_out_of_range_keep_counts(research_session):
    from smms.db.models import Signal, SignalType
    tokens, rows = seed_rows(research_session, [0, 100, None, 200])
    for i, row in enumerate(rows):
        row.features_json = {**row.features_json, "risk": "HIGH" if i == 3 else "LOW", "volume_5m": i * 10,
                             "smart_money_score": i * 20}
    signal = Signal(token_id=tokens[1].id, chain="bsc", created_at=AT, signal_type=SignalType.STRONG,
                    signal_strength=80)
    research_session.add(signal)
    research_session.flush()
    rows[1].latest_signal_id = signal.id
    research_session.flush()
    result = page(research_session, view="high_signal", limit=1)
    assert result["total"] == 1 and result["tab_counts"] == {"all": 4, "high_signal": 1, "watch": 2, "high_risk": 1}
    assert result["candidates"][0]["token_address"] == tokens[1].address
    empty = page(research_session, offset=500)
    assert empty["total"] == 4 and empty["count"] == 0 and empty["source_status"] == "OUT_OF_RANGE"
    assert empty["tab_counts"] == result["tab_counts"]
    for kwargs in ({"liquidity": 0}, {"smart_money": 20, "volume": 10}, {"age": 2}, {"risk": "LOW"}):
        assert page(research_session, **kwargs)["total"] == (2 if "age" in kwargs else 3)


def test_freshness_future_and_stale_candidates_are_explicit(research_session):
    _, rows = seed_rows(research_session, [1, 2, 3])
    rows[0].latest_seen_at = AT - timedelta(hours=2)
    rows[1].latest_seen_at = AT + timedelta(seconds=1)
    rows[2]._status = CandidateStatus.STALE
    research_session.flush()
    assert page(research_session)["total"] == 2
    assert page(research_session, include_stale=True)["total"] == 3
    assert page(research_session, freshness="stale")["total"] == 1
    assert page(research_session, freshness="unknown")["total"] == 1
    assert page(research_session, freshness="fresh", include_stale=True)["total"] == 1


def test_superseded_revision_during_hydration_rejects_read(research_session, monkeypatch):
    from sqlalchemy import update

    from smms.db.models import RadarFeatureObservation
    from smms.radar import views
    _, rows = seed_rows(research_session, [1])
    observation = add_projection(research_session, rows[0])
    original = views.candidate_view
    def replace(session, candidate, **kwargs):
        result = original(session, candidate, **kwargs)
        session.execute(update(RadarFeatureObservation).where(RadarFeatureObservation.id == observation.id)
                        .values(snapshot_status="SUPERSEDED").execution_options(synchronize_session=False))
        return result
    monkeypatch.setattr(views, "candidate_view", replace)
    with pytest.raises(ValueError, match="changed"):
        page(research_session)


@pytest.mark.asyncio
async def test_candidate_http_query_validation_and_rebuilding_error(monkeypatch):
    from contextlib import asynccontextmanager

    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from smms.api import radar
    from smms.radar.query import StaleCandidateRead

    class Session:
        async def run_sync(self, function):
            return function(self)
    @asynccontextmanager
    async def scope(_settings):
        yield Session()
    monkeypatch.setattr(radar, "session_scope", scope)
    app = FastAPI()
    app.include_router(radar.router)
    captured = {}
    def read(*args, **kwargs):
        captured.update(kwargs)
        return {"candidates": [], "total": 0, "count": 0}
    monkeypatch.setattr(radar, "candidates", read)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/radar/candidates", params={"q": "pair", "offset": 500, "view": "watch",
            "include_stale": "true", "sort": "volume", "direction": "asc", "liquidity": "0"})
        assert response.status_code == 200
        assert captured["offset"] == 500 and captured["q"] == "pair" and captured["view"] == "watch"
        assert captured["include_stale"] is True and captured["liquidity"] == 0
        for params in ({"liquidity": "NaN"}, {"volume": "Infinity"}, {"sort": "other"}, {"q": "x" * 201}):
            assert (await client.get("/api/radar/candidates", params=params)).status_code == 422
        def changed(*args, **kwargs):
            raise StaleCandidateRead("source epoch changed")
        monkeypatch.setattr(radar, "candidates", changed)
        response = await client.get("/api/radar/candidates")
        assert response.status_code == 409 and response.json()["detail"]["data_status"] == "REBUILDING"


@pytest.mark.parametrize("sort", ["priority", "age", "liquidity", "volume"])
@pytest.mark.parametrize("direction", ["asc", "desc"])
def test_all_sort_keys_precede_page_boundaries(research_session, sort, direction):
    tokens, rows = seed_rows(research_session, [30, 10, 20, 0])
    for row, score in zip(rows, [30, 10, 20, 0], strict=True):
        row.priority_json = {"priority_score": score}
        row.features_json = {**row.features_json, "volume_5m": score}
    research_session.flush()
    order = [0, 1, 2, 3] if sort == "age" else [3, 1, 2, 0]
    if direction == "desc":
        order.reverse()
    results = [page(research_session, limit=2, offset=offset, sort=sort, direction=direction) for offset in [0, 2]]
    assert [row["token_address"] for result in results for row in result["candidates"]] == [tokens[i].address for i in order]
    assert all(result["total"] == 4 for result in results)


def test_browser_read_rejects_a_canonical_commit_from_an_independent_session(research_session, monkeypatch):
    from sqlalchemy.orm import Session

    from smms.radar import views
    from smms.radar.query import StaleCandidateRead
    from smms.research.state import invalidate_derived

    if research_session.get_bind().dialect.name != "postgresql":
        pytest.skip("requires isolated PostgreSQL for independent committed transactions")
    _, rows = seed_rows(research_session, [1])
    add_projection(research_session, rows[0])
    research_session.commit()
    original = views.candidate_view
    def corrected(session, candidate, **kwargs):
        result = original(session, candidate, **kwargs)
        with Session(session.get_bind()) as writer:
            invalidate_derived(writer, reason="TEST_BROWSER_REORG_COMMIT", cause={})
            writer.commit()
        return result
    monkeypatch.setattr(views, "candidate_view", corrected)
    with pytest.raises(StaleCandidateRead, match="changed"):
        page(research_session)


@pytest.mark.parametrize("value", ["1e-11", "1e100", "1e-100"])
def test_finite_scientific_metrics_preserve_value_in_selection_and_payload(research_session, value):
    from decimal import Decimal
    _, rows = seed_rows(research_session, [value])
    result = page(research_session, liquidity=float(value))
    assert result["total"] == 1
    assert Decimal(str(result["candidates"][0]["features"]["liquidity"])) == Decimal(value)
    assert rows[0].features_json["liquidity"] == value


def test_explicit_zero_priority_minimum_excludes_unknown_but_retains_zero(research_session):
    _, rows = seed_rows(research_session, [1, 1])
    for row, score in zip(rows, ["UNKNOWN", 0], strict=True):
        row.priority_json = {"priority_score": score}
    research_session.flush()
    assert page(research_session)["total"] == 2
    assert page(research_session, min_priority=0)["total"] == 1
    assert page(research_session, priority=0)["total"] == 1


def test_unrepresentable_metrics_stay_unknown_without_overflow(research_session):
    seed_rows(research_session, ["1e999", "1e-999", "1.9e308", "NaN", "Infinity"])
    result = page(research_session)
    assert all(row["features"]["liquidity"] == "UNKNOWN" for row in result["candidates"])
    assert page(research_session, liquidity=0)["total"] == 0

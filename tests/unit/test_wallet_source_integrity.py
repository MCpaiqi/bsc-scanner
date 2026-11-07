"""Fresh canonical facts, exact published profiles and explicit USD uncertainty."""

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_alpha_collector import coverage_session as _coverage_session
from test_derived_snapshot_integrity import PAIR, T0, TOKEN, WALLET, fact, seed
from test_research_integrity import research_session as _research_session

from smms.db.models import Pair, TokenSmartMoneySnapshot, TradeEvent, WalletSmartMoneySnapshot
from smms.research.state import invalidate_derived
from smms.smart_money.aggregation import IntelligenceAggregationService
from smms.smart_money.profile import build_wallet_profile

research_session = _research_session
coverage_session = _coverage_session
CUTOFF = T0 + timedelta(seconds=20)


def refresh(session, path="all"):
    service = IntelligenceAggregationService(session, min_wallet_score=0)
    if path == "token":
        return service.refresh_token(TOKEN, as_of=CUTOFF)
    if path == "wallet":
        return service.refresh_wallet(WALLET, as_of=CUTOFF)
    return service.refresh(as_of=CUTOFF)


@pytest.mark.parametrize("path", ["all", "token", "wallet"])
def test_refresh_uses_corrected_persisted_values_despite_retained_identity_map(research_session, path):
    seed(research_session)
    held = fact(research_session, value=10)
    research_session.commit()
    refresh(research_session, path)
    research_session.commit()
    assert held.value_usd == 10
    pair = research_session.scalar(select(Pair).where(Pair.address == PAIR))
    assert pair.creation_timestamp.replace(tzinfo=T0.tzinfo) == T0
    with Session(research_session.bind) as writer:
        source = writer.scalar(select(TradeEvent))
        source.value_usd = 20
        source_pair = writer.scalar(select(Pair).where(Pair.address == PAIR))
        source_pair.creation_timestamp = T0 + timedelta(seconds=5)
        invalidate_derived(writer, reason="TEST_CORRECTION", cause={})
        writer.commit()
    refresh(research_session, path)
    current = research_session.scalar(select(WalletSmartMoneySnapshot))
    assert Decimal(current.profile_json["total_invested"]) == 20
    assert current.input_manifest["facts"]["trades"][0]["value_usd"] == "20"
    assert current.input_manifest["facts"]["pairs"][0]["creation_timestamp"] == (T0 + timedelta(seconds=5)).isoformat(timespec="microseconds")
    if path != "wallet":
        assert research_session.scalar(select(TokenSmartMoneySnapshot)).smart_money_net_flow == 20


def test_native_amount_cannot_be_a_dollar_cost():
    profile = build_wallet_profile(WALLET, trades=[{
        "wallet_address": WALLET, "token_address": TOKEN, "timestamp": T0, "side": "BUY",
        "token_amount": 1, "native_amount": 10, "price": 999,
    }])
    data = profile.to_dict()
    assert data["roi"] is None and data["total_invested"] is None
    assert data["data_status"] == "UNKNOWN" and data["unvalued_positions"] == 1


def test_canonical_profile_marks_usd_instead_of_generic_quote(research_session):
    seed(research_session)
    trade = fact(research_session, value=10)
    trade.price, trade.price_usd = 999, 10
    refresh(research_session)
    wallet = research_session.scalar(select(WalletSmartMoneySnapshot))
    assert Decimal(wallet.profile_json["roi"]) == 0
    assert wallet.input_manifest["parameters"]["input_policy"] == "canonical-wallet-usd-v2"


def test_dedicated_usd_value_wins_unversioned_metadata_cache(research_session):
    seed(research_session)
    trade = fact(research_session, value=10)
    trade.metadata_json = {"usd_value": "999"}
    refresh(research_session)
    assert research_session.scalar(select(TokenSmartMoneySnapshot)).smart_money_net_flow == 10


def test_unknown_usd_survives_token_materialization_as_null(research_session):
    seed(research_session)
    trade = fact(research_session)
    trade.value_usd, trade.native_amount, trade.price = None, 50, 999
    refresh(research_session)
    row = research_session.scalar(select(TokenSmartMoneySnapshot))
    assert row.smart_money_buy_usd is None and row.smart_money_net_flow is None
    assert row.data_status in {"UNKNOWN", "DEGRADED"}
    wallet = research_session.scalar(select(WalletSmartMoneySnapshot))
    assert wallet.profile_json["roi"] is None and wallet.confidence_score == 0


def test_partial_usd_does_not_claim_a_complete_net_flow(research_session):
    seed(research_session)
    fact(research_session, value=10)
    missing = fact(research_session, tx="unvalued-sale", side="SELL", at=T0 + timedelta(seconds=15))
    missing.value_usd = None
    refresh(research_session)
    row = research_session.scalar(select(TokenSmartMoneySnapshot))
    assert row.smart_money_buy_usd == 10 and row.smart_money_sell_usd is None
    assert row.smart_money_net_flow is None and row.data_status == "DEGRADED"


def test_a_preserved_newer_wallet_winner_cannot_mislabel_computed_inputs(research_session, monkeypatch):
    from smms.db.repositories import WalletSmartMoneySnapshotRepository

    seed(research_session)
    fact(research_session, value=10)
    refresh(research_session)
    winner = research_session.scalar(select(WalletSmartMoneySnapshot))
    research_session.commit()
    trade = research_session.scalar(select(TradeEvent))
    trade.value_usd = 20
    research_session.flush()

    monkeypatch.setattr(WalletSmartMoneySnapshotRepository, "upsert", lambda _self, record: winner)
    with pytest.raises(ValueError, match="(?i)(input|epoch|winner|publication)"):
        refresh(research_session)
    current = research_session.scalar(select(TokenSmartMoneySnapshot))
    assert current.smart_money_net_flow == 10


def test_real_publisher_new_epoch_winner_aborts_old_token_computation(research_session, monkeypatch):
    from smms.db.repositories import WalletSmartMoneySnapshotRepository

    seed(research_session)
    source = fact(research_session, value=10)
    refresh(research_session)
    research_session.commit()
    original = WalletSmartMoneySnapshotRepository.upsert
    interleaved = False

    def supersede(repository, computed):
        nonlocal interleaved
        if not interleaved:
            interleaved = True
            source.value_usd = 20
            invalidate_derived(research_session, reason="TEST_INTERLEAVED_CORRECTION", cause={})
            refresh(research_session, "wallet")
        return original(repository, computed)

    monkeypatch.setattr(WalletSmartMoneySnapshotRepository, "upsert", supersede)
    with pytest.raises(ValueError, match="(?i)(epoch|winner|inputs)"):
        refresh(research_session, "token")
    winner = research_session.scalar(select(WalletSmartMoneySnapshot))
    assert Decimal(winner.profile_json["total_invested"]) == 20
    assert research_session.scalar(select(TokenSmartMoneySnapshot)) is None
    history = research_session.scalars(select(TokenSmartMoneySnapshot).execution_options(include_invalid_snapshots=True)).all()
    assert len(history) == 1 and history[0].smart_money_net_flow == 10


def test_cached_creation_and_future_outcome_labels_are_not_profile_facts(research_session):
    seed(research_session)
    row = fact(research_session)
    row.metadata_json = {"token_created_at": (T0 + timedelta(days=1)).isoformat(),
                         "entry_delay_seconds": 999999, "outcome_class": "RUG"}
    refresh(research_session)
    wallet = research_session.scalar(select(WalletSmartMoneySnapshot))
    assert wallet.profile_json["early_entry_rate"] == "1"
    assert wallet.profile_json["rug_exposure"] == "0"
    assert wallet.input_manifest["facts"]["normalized_trades"][0]["metadata_json"] == {}


def test_zero_observed_dollars_is_distinct_from_missing_value(research_session):
    seed(research_session)
    fact(research_session, value=0)
    refresh(research_session)
    token = research_session.scalar(select(TokenSmartMoneySnapshot))
    assert token.smart_money_buy_usd == token.smart_money_net_flow == 0
    assert token.data_status == "CHAIN_EVENTS"


@pytest.mark.parametrize("missing", [False, True])
def test_canonical_token_outputs_reach_radar_with_explicit_unknowns(research_session, missing):
    from smms.radar.engine import RadarEngine
    from smms.radar.features import UNKNOWN

    seed(research_session)
    fact(research_session, value=10)
    if missing:
        sale = fact(research_session, tx="unpriced-sale", side="SELL", at=T0 + timedelta(seconds=15))
        sale.value_usd = None
    refresh(research_session)
    candidate = RadarEngine(research_session).ingest(research_session.scalar(select(Pair)), at=CUTOFF)[0]
    assert candidate.features_json["known_smart_wallet_count"] == 1
    flow = candidate.features_json["smart_money_net_flow"]
    assert flow == UNKNOWN if missing else Decimal(flow) == 10


def test_known_cash_flow_does_not_require_inventory_quantity():
    data = build_wallet_profile(WALLET, trades=[{
        "wallet_address": WALLET, "token_address": TOKEN, "timestamp": T0, "side": "BUY",
        "token_amount": None, "usd_value": 10,
    }, {
        "wallet_address": WALLET, "token_address": TOKEN, "timestamp": CUTOFF, "side": "SELL",
        "token_amount": None, "usd_value": 15,
    }]).to_dict()
    assert data["roi"] is None and data["data_status"] == "UNKNOWN"
    assert data["known_invested_usd"] == 10 and data["known_returned_usd"] == 15


@pytest.mark.parametrize("metadata", [["historical-import"], "legacy", 1])
def test_nonobject_metadata_does_not_abort_canonical_refresh(research_session, metadata):
    seed(research_session)
    row = fact(research_session, value=10)
    row.metadata_json = metadata
    refresh(research_session)
    assert research_session.scalar(select(TokenSmartMoneySnapshot)).smart_money_net_flow == 10
    profile = build_wallet_profile(WALLET, trades=[{
        "wallet_address": WALLET, "token_address": TOKEN, "timestamp": T0, "side": "BUY",
        "token_amount": 1, "usd_value": 10, "metadata_json": metadata,
    }])
    assert profile.to_dict()["known_invested_usd"] == 10


@pytest.mark.asyncio
async def test_api_keeps_unknown_values_and_ranks_unknown_roi_last(coverage_session, monkeypatch):
    from contextlib import asynccontextmanager

    from httpx import ASGITransport, AsyncClient

    from smms.api import app as api
    from smms.research.lineage import manifest
    from smms.research.publication import publish_snapshot

    session = coverage_session
    await session.run_sync(seed)
    row = await session.run_sync(fact)
    row.value_usd, row.native_amount, row.price = None, 1000, 999
    await session.run_sync(refresh)
    await session.run_sync(lambda sync: publish_snapshot(sync, WalletSmartMoneySnapshot(
        chain="bsc", wallet_address="loss-wallet", as_of=CUTOFF, score_version="v2", profile_version="v1", score=0,
        confidence="LOW", confidence_score=0, sample_size=1, data_completeness=1, historical_coverage=1, trade_quality=1,
        profile_json={"roi": "-0.5", "data_status": "KNOWN"}, components_json={}, contributions_json={},
        input_manifest=manifest(kind="wallet_smart_money_snapshots", parameters={}, facts={}), source_epoch=0,
    )))
    await session.commit()

    @asynccontextmanager
    async def scope(_settings):
        yield session

    monkeypatch.setattr(api, "session_scope", scope)
    retained = await session.scalar(select(WalletSmartMoneySnapshot).where(WalletSmartMoneySnapshot.wallet_address == WALLET))
    retained.profile_json["roi"] = "999"
    async with AsyncClient(transport=ASGITransport(app=api.app), base_url="http://test") as client:
        token = (await client.get(f"/api/radar/tokens/{TOKEN}/smart-money")).json()["smart_money"]
        assert token["smart_money_buy_usd"] is None and token["smart_money_net_flow"] is None
        wallets = (await client.get("/api/radar/smart-money/wallets?sort=roi")).json()["wallets"]
        assert wallets[0]["wallet"] == "loss-wallet"
        assert wallets[1]["roi"] is None and Decimal(str(wallets[1]["confidence_score"])) == 0


def test_snapshot_reader_refreshes_profile_json_from_persisted_data(research_session):
    from smms.db.repositories import WalletSmartMoneySnapshotRepository

    seed(research_session)
    fact(research_session)
    refresh(research_session)
    research_session.commit()
    row = research_session.scalar(select(WalletSmartMoneySnapshot))
    row.profile_json["roi"] = "999"
    result = WalletSmartMoneySnapshotRepository(research_session).get_latest(WALLET, chain="bsc", score_version="v2", as_of=CUTOFF)
    assert Decimal(result.profile_json["roi"]) == 0


def test_rollback_preserves_prior_generation_and_money(research_session):
    seed(research_session)
    trade = fact(research_session, value=10)
    refresh(research_session)
    original = research_session.scalar(select(TokenSmartMoneySnapshot)).id
    research_session.commit()
    trade.value_usd = 20
    refresh(research_session)
    assert research_session.scalar(select(TokenSmartMoneySnapshot)).smart_money_net_flow == 20
    research_session.rollback()
    current = research_session.scalar(select(TokenSmartMoneySnapshot))
    assert current.id == original and current.smart_money_net_flow == 10


def test_postgres_old_publication_cannot_bind_a_newer_wallet_winner(research_session, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from smms.db.repositories import WalletSmartMoneySnapshotRepository

    if research_session.bind.dialect.name != "postgresql":
        pytest.skip("independent publication writers require PostgreSQL")
    seed(research_session)
    fact(research_session, value=10)
    refresh(research_session)
    research_session.commit()
    ready, release = Event(), Event()
    original = WalletSmartMoneySnapshotRepository.upsert

    def pause(repository, record):
        if record.source_epoch == 0:
            ready.set()
            assert release.wait(timeout=8)
        return original(repository, record)

    monkeypatch.setattr(WalletSmartMoneySnapshotRepository, "upsert", pause)

    def old_writer():
        with Session(research_session.bind) as session:
            with pytest.raises(ValueError, match="(?i)(epoch|winner|inputs)"):
                refresh(session, "token")
            session.rollback()

    with ThreadPoolExecutor(max_workers=1) as pool:
        job = pool.submit(old_writer)
        try:
            assert ready.wait(timeout=5)
            with Session(research_session.bind) as writer:
                source = writer.scalar(select(TradeEvent))
                source.value_usd = 20
                invalidate_derived(writer, reason="TEST_CORRECTION", cause={})
                refresh(writer, "token")
                writer.commit()
        finally:
            release.set()
        job.result(timeout=15)
    current = research_session.scalar(select(TokenSmartMoneySnapshot))
    assert current.smart_money_net_flow == 20
    history = research_session.scalars(select(TokenSmartMoneySnapshot).execution_options(include_invalid_snapshots=True)).all()
    assert len(history) == 2

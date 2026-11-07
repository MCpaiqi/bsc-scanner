"""Canonical Radar/Signal snapshots, immutable lineage and dependency recovery."""

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from test_derived_snapshot_integrity import T0, TOKEN, fact, seed
from test_research_integrity import research_session as _research_session

from smms.db.models import Pair, Signal, Token, TradeEvent
from smms.radar.engine import RadarEngine
from smms.radar.live_signal import LiveSignalService
from smms.radar.views import candidate_view
from smms.research.rebuild import rebuild_due
from smms.research.state import invalidate_derived

research_session = _research_session
CUTOFF = T0 + timedelta(seconds=20)


def candidate(session):
    seed(session)
    fact(session)
    pair = session.scalar(select(Pair))
    return RadarEngine(session).ingest(pair, at=CUTOFF)[0]


def test_view_withholds_invalidated_cached_features_and_signal(research_session):
    row = candidate(research_session)
    LiveSignalService(research_session).materialize(row, as_of=CUTOFF)
    invalidate_derived(research_session, reason="TEST_REORG", cause={})
    research_session.commit()
    view = candidate_view(research_session, row)
    assert view["data_status"] == "REBUILDING"
    assert view["features"]["buy_count_5m"] == "UNKNOWN"
    assert view["latest_signal_class"] == "UNKNOWN" and view["priority_score"] == 0


def test_signal_replays_current_facts_instead_of_candidate_cache(research_session):
    row = candidate(research_session)
    row.features_json = {**row.features_json, "buy_count_5m": 999, "smart_money_score": "100", "risk": "LOW"}
    signal = LiveSignalService(research_session).materialize(row, as_of=CUTOFF)
    assert signal.smart_money_score is None
    assert signal.risk_level == "UNKNOWN"
    assert signal.input_manifest["digest"]


def test_same_time_signal_refresh_appends_corrected_revision(research_session):
    row = candidate(research_session)
    service = LiveSignalService(research_session)
    original = service.materialize(row, as_of=CUTOFF)
    assert original.market_score == 100
    fact(research_session, tx="sale", side="SELL", at=T0 + timedelta(seconds=15))
    current = service.materialize(row, as_of=CUTOFF)
    assert current.id != original.id and original.market_score == 100 and current.market_score == 50
    assert current.previous_snapshot_id == original.id


def test_radar_price_uses_explicit_usd_and_never_generic_quote(research_session):
    row = candidate(research_session)
    trade = research_session.scalar(select(TradeEvent))
    trade.price, trade.price_usd, trade.value_usd = 99, 2, None
    RadarEngine(research_session).refresh(row, at=CUTOFF)
    assert Decimal(row.features_json["price"]) == 2


def test_orphaned_pair_cannot_keep_candidate_or_signal_current(research_session):
    row = candidate(research_session)
    original = LiveSignalService(research_session).materialize(row, as_of=CUTOFF)
    pair = research_session.scalar(select(Pair))
    pair.canonical, pair.removed = False, True
    invalidate_derived(research_session, reason="TEST_REORG", cause={})
    RadarEngine(research_session).refresh(row, at=CUTOFF)
    assert row.data_status == "ORPHANED"
    assert research_session.scalar(select(Signal).where(Signal.id == original.id)) is None


def test_rebuild_preserves_signal_history_and_original_cutoff(research_session):
    row = candidate(research_session)
    old = LiveSignalService(research_session).materialize(row, as_of=CUTOFF)
    old_trade = research_session.scalar(select(TradeEvent))
    old_trade.canonical, old_trade.removed = False, True
    fact(research_session, tx="replacement", side="SELL")
    fact(research_session, tx="future", at=CUTOFF + timedelta(hours=1))
    invalidate_derived(research_session, reason="TEST_REORG", cause={})
    research_session.commit()
    for _ in range(5):
        rebuild_due(research_session, now=CUTOFF + timedelta(days=1))
        research_session.commit()
    current = research_session.scalar(select(Signal).where(Signal.created_at == CUTOFF))
    assert current is not None and current.id != old.id
    assert current.market_score == 0 and old.market_score == 100
    assert "future" not in str(current.input_manifest)


def test_candidate_view_does_not_resolve_unversioned_token_evidence_cache(research_session):
    row = candidate(research_session)
    token = research_session.get(Token, row.token_id)
    token.metadata_json = {"evidence": {"is_honeypot": {
        "value": True, "resolved_value": True, "source": "CHAIN", "evidence_level": "ON_CHAIN_VERIFIED",
    }}}
    view = candidate_view(research_session, row)
    assert "is_honeypot" not in view["evidence"]


def test_stored_chain_evidence_without_canonical_reference_cannot_resurrect(research_session):
    from smms.db.models import EvidenceObservationRecord
    from smms.external_evidence.orchestrator import ExternalEvidenceOrchestrator

    seed(research_session)
    research_session.add(EvidenceObservationRecord(
        chain="bsc", subject_type="TOKEN", subject_address=TOKEN, field="is_honeypot", value_json=True,
        source="old-chain", source_type="CHAIN", observed_at=T0, fetched_at=T0,
        evidence_level="ON_CHAIN_VERIFIED", raw_reference_json={}, stale=False,
    ))
    research_session.flush()
    observations = ExternalEvidenceOrchestrator(research_session, providers=[]).load("bsc", TOKEN, as_of=CUTOFF)
    assert observations == []


def test_live_view_uses_snapshot_instead_of_tampered_mutable_cache(research_session):
    row = candidate(research_session)
    row.features_json = {**row.features_json, "smart_money_score": "100", "risk": "LOW"}
    row.priority_score = 100
    view = candidate_view(research_session, row)
    assert view["features"]["smart_money_score"] == "UNKNOWN" and view["features"]["risk"] == "UNKNOWN"
    assert view["priority_score"] < 100 and view["research"]["digest"]


def test_older_rebuild_does_not_rewind_newer_live_projection(research_session):
    row = candidate(research_session)
    LiveSignalService(research_session).materialize(row, as_of=CUTOFF)
    invalidate_derived(research_session, reason="TEST_REORG", cause={})
    later = CUTOFF + timedelta(seconds=10)
    live = LiveSignalService(research_session).materialize(row, as_of=later)
    live_projection = row.projection_id
    research_session.commit()
    for _ in range(4):
        rebuild_due(research_session, now=CUTOFF + timedelta(days=1))
        research_session.commit()
    research_session.refresh(row)
    assert row.projection_id == live_projection and row.latest_signal_id == live.id
    assert candidate_view(research_session, row)["research"]["source_epoch"] == live.source_epoch


def test_signal_reconstruction_waits_for_prerequisite_projection(research_session):
    from smms.db.models import ResearchRebuildJob
    from smms.research.state import capture_epoch

    row = candidate(research_session)
    signal = LiveSignalService(research_session).materialize(row, as_of=CUTOFF)
    epoch = invalidate_derived(research_session, reason="TEST_REORG", cause={})
    now = CUTOFF + timedelta(days=1)
    job = ResearchRebuildJob(kind="signals", snapshot_id=signal.id, requested_epoch=epoch,
                            status="PENDING", attempts=0, next_attempt_at=now - timedelta(seconds=1))
    research_session.add(job)
    research_session.commit()
    rebuild_due(research_session, now=now, batch_size=1)
    assert job.status == "PENDING" and job.last_error == "DependenciesPending"
    assert research_session.scalar(select(Signal)) is None
    for _ in range(4):
        rebuild_due(research_session, now=now + timedelta(minutes=2), batch_size=16)
        research_session.commit()
    assert job.status == "COMPLETE"
    current = research_session.scalar(select(Signal))
    assert current.source_epoch == capture_epoch(research_session)


def test_orphan_rebuild_retains_unavailable_signal_history(research_session):
    row = candidate(research_session)
    old = LiveSignalService(research_session).materialize(row, as_of=CUTOFF)
    pair = research_session.scalar(select(Pair))
    pair.canonical, pair.removed = False, True
    invalidate_derived(research_session, reason="TEST_REORG", cause={})
    research_session.commit()
    for _ in range(4):
        rebuild_due(research_session, now=CUTOFF + timedelta(days=1))
        research_session.commit()
    assert research_session.scalar(select(Signal)) is None
    history = research_session.scalars(select(Signal).order_by(Signal.research_revision)
                                       .execution_options(include_invalid_snapshots=True)).all()
    assert len(history) == 2 and history[0].market_score == old.market_score
    assert history[1].snapshot_status == "UNAVAILABLE" and history[1].previous_snapshot_id == old.id


def test_epoch_change_during_signal_read_requires_retry(research_session, monkeypatch):
    from smms.radar.projection import StaleProjectionInputs

    row = candidate(research_session)
    original = RadarEngine.features

    def change_epoch(engine, *args, **kwargs):
        result = original(engine, *args, **kwargs)
        invalidate_derived(engine.session, reason="TEST_INTERLEAVE", cause={})
        return result

    monkeypatch.setattr(RadarEngine, "features", change_epoch)
    with pytest.raises(StaleProjectionInputs):
        LiveSignalService(research_session).materialize(row, as_of=CUTOFF)
    assert research_session.scalar(select(Signal)) is None


def test_chain_observation_cannot_be_relabelled_after_reorg(research_session):
    from smms.evidence import EvidenceResolver
    from smms.external_evidence.chain import canonical_observations
    from smms.external_evidence.orchestrator import ExternalEvidenceOrchestrator
    from smms.risk.aggregation import RiskAggregationService

    row = candidate(research_session)
    observations = canonical_observations(RadarEngine(research_session), row, as_of=CUTOFF)
    orchestrator = ExternalEvidenceOrchestrator(research_session, providers=[])
    for item in observations:
        orchestrator._persist(item)
    research_session.flush()
    assert orchestrator.load("bsc", TOKEN, as_of=CUTOFF)
    resolved = EvidenceResolver().resolve(observations, now=CUTOFF)
    invalidate_derived(research_session, reason="TEST_REORG", cause={})
    RiskAggregationService(research_session).refresh(as_of=CUTOFF, token_address=TOKEN, resolved_evidence=resolved)
    from smms.db.models import TokenRiskSnapshot

    risk = research_session.scalar(select(TokenRiskSnapshot))
    assert risk.input_manifest["facts"]["resolved_evidence"] == {}
    assert orchestrator.load("bsc", TOKEN, as_of=CUTOFF) == []


def test_chain_source_correction_rejects_old_fingerprint_without_epoch_change(research_session):
    from smms.external_evidence.chain import canonical_observations, eligible

    row = candidate(research_session)
    observation = canonical_observations(RadarEngine(research_session), row, as_of=CUTOFF)[0]
    assert eligible(research_session, observation, chain="bsc", token=TOKEN, as_of=CUTOFF)
    pair = research_session.scalar(select(Pair))
    pair.metadata_json = {"orphaned": True}
    research_session.flush()
    assert not eligible(research_session, observation, chain="bsc", token=TOKEN, as_of=CUTOFF)


def test_custom_signal_recipe_survives_reconstruction(research_session):
    from smms.backtest.signal_engine import SignalWeights

    row = candidate(research_session)
    old = LiveSignalService(research_session, weights=SignalWeights.from_mapping({"market": "0.9"}),
                            threshold=17, config_version="custom-policy").materialize(row, as_of=CUTOFF)
    invalidate_derived(research_session, reason="TEST_REORG", cause={})
    research_session.commit()
    for _ in range(4):
        rebuild_due(research_session, now=CUTOFF + timedelta(days=1))
        research_session.commit()
    current = research_session.scalar(select(Signal))
    assert current.config_hash == old.config_hash and current.signal_strength == old.signal_strength
    assert current.input_manifest["parameters"]["threshold"] == "17"


def test_forward_writer_does_not_capture_stale_projection(research_session):
    from smms.radar.live_forward import record_observation

    row = candidate(research_session)
    invalidate_derived(research_session, reason="TEST_REORG", cause={})
    assert record_observation(research_session, row, "WATCH", at=CUTOFF) is None


def test_recent_sell_refreshes_observation_time_without_becoming_discovery(research_session):
    from smms.evaluation import utc

    row = candidate(research_session)
    later = CUTOFF + timedelta(hours=2)
    recent = fact(research_session, tx="recent-sale", side="SELL", at=later - timedelta(seconds=1))
    RadarEngine(research_session).refresh(row, at=later)
    assert utc(row.latest_seen_at) == utc(recent.timestamp)
    assert row.status.value != "STALE"


def test_changed_discovery_side_cannot_keep_a_trade_only_candidate(research_session):
    from smms.db.models import RadarCandidate, RadarCandidateEvidence

    seed(research_session)
    trade = fact(research_session)
    row = RadarCandidate(token_id=research_session.scalar(select(Token.id).where(Token.address == TOKEN)),
                         discovered_at=T0, latest_seen_at=CUTOFF, discovery_source="FIRST_TRADE", discovery_reason="fixture")
    research_session.add(row)
    research_session.flush()
    research_session.add(RadarCandidateEvidence(candidate_id=row.id, source_key=f"trade:{trade.id}",
                                                source_kind="FIRST_TRADE", trade_id=trade.id, valid=True, raw_required=False))
    research_session.flush()
    trade.side = "SELL"
    RadarEngine(research_session).refresh(row, at=CUTOFF)
    assert row.data_status == "ORPHANED"


def test_unversioned_launchpad_cache_cannot_label_a_canonical_pair_candidate(research_session):
    row = candidate(research_session)
    token = research_session.get(Token, row.token_id)
    token.launchpad = "orphan-platform"
    assert candidate_view(research_session, row)["launchpad"] == "UNKNOWN"


def test_alerts_do_not_trigger_from_invalidated_or_forged_cache_values(research_session):
    from smms.radar.alerts import create_rule, evaluate_enabled_rules

    row = candidate(research_session)
    create_rule(research_session, {"name": "smart entry", "conditions": [
        {"field": "smart_money_score", "operator": ">", "value": 50},
    ]})
    forged = {**row.features_json, "smart_money_score": "100"}
    assert evaluate_enabled_rules(research_session, chain="bsc", token_address=TOKEN, state=forged, at=CUTOFF) == []
    invalidate_derived(research_session, reason="TEST_REORG", cause={})
    assert evaluate_enabled_rules(research_session, chain="bsc", token_address=TOKEN, state=forged, at=CUTOFF) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit_cutoff", [False, True])
async def test_live_provider_collection_uses_completion_time_but_historical_cutoff_stays_fixed(research_session, explicit_cutoff):
    import asyncio
    from datetime import UTC, datetime

    from smms.external_evidence import Capability, EvidenceObservation
    from smms.external_evidence.orchestrator import ExternalEvidenceOrchestrator

    class Provider:
        provider_name = "fixture"
        capabilities = frozenset({Capability.TOKEN_MARKET})

        async def fetch_token_market(self, chain, token):
            await asyncio.sleep(0.001)
            fetched = datetime.now(UTC)
            return [EvidenceObservation(chain=chain, subject_type="TOKEN", subject_address=token,
                                        field="price_usd", value=2, source=self.provider_name,
                                        observed_at=fetched, fetched_at=fetched, evidence_level="TRUSTED_PROVIDER")]

    cutoff = datetime.now(UTC) if explicit_cutoff else None
    resolved = await ExternalEvidenceOrchestrator(research_session, providers=[Provider()]).refresh("bsc", TOKEN, as_of=cutoff)
    assert ("price_usd" in resolved) is (not explicit_cutoff)


def test_two_postgres_signal_writers_converge_without_duplicate_revisions(research_session, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from sqlalchemy.orm import Session

    from smms.db.models import RadarCandidate, RadarFeatureObservation
    from smms.radar import projection

    if research_session.bind.dialect.name != "postgresql":
        pytest.skip("independent source publishers require PostgreSQL")
    row = candidate(research_session)
    row_id = row.id
    research_session.commit()
    ready = Barrier(2)
    monkeypatch.setattr(projection, "apply_projection", lambda *args: False)

    def publish():
        with Session(research_session.bind) as session:
            value = session.get(RadarCandidate, row_id)
            ready.wait(timeout=5)
            # Build immutable records without contending over the operational
            # candidate's optimistic cache write in this publication regression.
            snapshot = LiveSignalService(session).materialize(value, as_of=CUTOFF)
            session.commit()
            return snapshot.id

    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(publish) for _ in range(2)]
        identities = [job.result(timeout=15) for job in jobs]
    assert identities[0] == identities[1]
    rows = research_session.scalars(select(RadarFeatureObservation).execution_options(include_invalid_snapshots=True)).all()
    assert len(rows) == 1
    assert len(research_session.scalars(select(Signal).execution_options(include_invalid_snapshots=True)).all()) == 1


def test_postgres_reorg_can_commit_before_old_projection_publication(research_session, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from sqlalchemy import text
    from sqlalchemy.orm import Session

    from smms.db.models import RadarCandidate
    from smms.radar import projection

    if research_session.bind.dialect.name != "postgresql":
        pytest.skip("independent reorg writer requires PostgreSQL")
    row = candidate(research_session)
    row_id = row.id
    research_session.commit()
    captured, release = Event(), Event()
    original = projection.publish_snapshot

    def pause(session, value):
        captured.set()
        assert release.wait(timeout=8)
        return original(session, value)

    monkeypatch.setattr(projection, "publish_snapshot", pause)

    def publish():
        with Session(research_session.bind) as session:
            value = session.get(RadarCandidate, row_id)
            projection.build_projection(RadarEngine(session), value, at=CUTOFF)
            session.commit()

    with ThreadPoolExecutor(max_workers=1) as pool:
        job = pool.submit(publish)
        try:
            assert captured.wait(timeout=5)
            research_session.execute(text("SET LOCAL lock_timeout = '3s'"))
            invalidate_derived(research_session, reason="TEST_REORG", cause={})
            research_session.commit()
        finally:
            release.set()
        job.result(timeout=15)
    assert candidate_view(research_session, row)["data_status"] == "REBUILDING"


def test_same_epoch_risk_correction_invalidates_and_rebuilds_transitive_outputs(research_session):
    from smms.db.models import TokenRiskSnapshot
    from smms.db.repositories import TokenRiskSnapshotRepository
    from smms.research.lineage import manifest

    row = candidate(research_session)
    repo = TokenRiskSnapshotRepository(research_session)

    def risk(level):
        return repo.upsert(TokenRiskSnapshot(
            chain="bsc", token_address=TOKEN, as_of=CUTOFF, risk_version="risk-v1", source_epoch=0,
            overall_risk=level, contract_risk=level, liquidity_risk=level, holder_risk=level, behavior_risk=level,
            confidence="HIGH", input_manifest=manifest(kind="token_risk_snapshots", parameters={}, facts={"risk": level}),
        ))

    risk("HIGH")
    old = LiveSignalService(research_session).materialize(row, as_of=CUTOFF)
    assert old.risk_level == "HIGH"
    risk("LOW")
    view = candidate_view(research_session, row)
    assert view["data_status"] == "REBUILDING" and view["latest_signal_class"] == "UNKNOWN"
    assert research_session.scalar(select(Signal)) is None
    for _ in range(4):
        rebuild_due(research_session, now=CUTOFF + timedelta(days=1))
        research_session.commit()
    current = research_session.scalar(select(Signal))
    assert current.risk_level == "LOW" and current.id != old.id and old.risk_level == "HIGH"


def test_backdated_market_asof_cannot_admit_a_future_observation(research_session):
    from smms.db.models import TokenMarketSnapshot
    from smms.research.publication import publish_snapshot

    row = candidate(research_session)
    trade = research_session.scalar(select(TradeEvent))
    trade.value_usd, trade.price_usd = None, None
    publish_snapshot(research_session, TokenMarketSnapshot(
        chain="bsc", chain_id=56, token_address=TOKEN, pair_address=research_session.scalar(select(Pair.address)),
        as_of=CUTOFF, observed_at=CUTOFF + timedelta(hours=1), source="DEXSCREENER", source_epoch=-1,
        status="KNOWN", price_usd=123,
    ))
    signal = LiveSignalService(research_session).materialize(row, as_of=CUTOFF)
    assert signal.entry_price is None


@pytest.mark.parametrize("source_type,level", [("RPC", "VERIFIED"), ("ON_CHAIN", "ON_CHAIN_VERIFIED"),
                                                ("BSC_RPC", "custom-level"), ("DERIVED_CHAIN", "custom-level")])
def test_chain_aliases_cannot_bypass_source_proof(research_session, source_type, level):
    from smms.external_evidence import EvidenceObservation
    from smms.external_evidence.chain import eligible

    item = EvidenceObservation(chain="bsc", subject_type="TOKEN", subject_address=TOKEN, field="mintable",
                               value=False, source="pretend-chain", source_type=source_type, evidence_level=level,
                               observed_at=T0, fetched_at=T0)
    assert not eligible(research_session, item, chain="bsc", token=TOKEN, as_of=CUTOFF)


def test_latest_provider_observation_wins_without_conflict_with_its_own_history(research_session):
    from datetime import UTC, datetime

    from smms.external_evidence import EvidenceObservation
    from smms.external_evidence.orchestrator import ExternalEvidenceOrchestrator

    row = candidate(research_session)
    now = datetime.now(UTC)
    orchestrator = ExternalEvidenceOrchestrator(research_session, providers=[])
    for at, value in ((now - timedelta(hours=1), True), (now, False)):
        orchestrator._persist(EvidenceObservation(chain="bsc", subject_type="TOKEN", subject_address=TOKEN,
                                                  field="mintable", value=value, source="goplus",
                                                  evidence_level="TRUSTED_PROVIDER", observed_at=at, fetched_at=at))
    view = candidate_view(research_session, row)
    assert view["evidence"]["mintable"]["resolved_value"] is False
    assert view["evidence"]["mintable"]["freshness"] == "FRESH"
    assert not view["evidence"]["mintable"]["conflict"]


def test_configured_discovery_threshold_survives_default_signal_and_evidence_paths(research_session):
    from smms.external_evidence.chain import canonical_observations
    from smms.radar.config import RadarConfig

    seed(research_session)
    trade = fact(research_session, value=5)
    engine = RadarEngine(research_session, RadarConfig(min_trade_usd=1))
    row = engine.ingest(trade, at=CUTOFF)[0]
    assert row.data_status != "ORPHANED"
    signal = LiveSignalService(research_session).materialize(row, as_of=CUTOFF)
    assert signal.snapshot_status == "VALID" and row.data_status != "ORPHANED"
    assert canonical_observations(RadarEngine(research_session), row, as_of=CUTOFF)


def test_sampled_delayed_discovery_keeps_baseline_at_original_cutoff(research_session, monkeypatch):
    from smms.db.models import RadarFeatureObservation
    from smms.radar import live_forward

    row = candidate(research_session)
    monkeypatch.setattr(live_forward, "_sample_rejected", lambda *args, **kwargs: True)
    observation = live_forward.record_observation(research_session, row, "MARKET_BASELINE", at=T0)
    assert observation is not None and observation.observed_at == T0
    projection = research_session.scalar(select(RadarFeatureObservation).where(
        RadarFeatureObservation.id == observation.t0_snapshot_json["projection_id"],
    ))
    from smms.evaluation import utc

    assert utc(projection.as_of) == T0
    assert projection.features_json["buy_count_5m"] == "UNKNOWN"


def test_projection_pointer_checks_epoch_in_the_same_select(research_session):
    from sqlalchemy import event

    from smms.db.models import ResearchState
    from smms.radar.projection import projection_for

    research_session.add(ResearchState(chain="bsc", epoch=1))
    research_session.flush()
    row = candidate(research_session)
    fired = []

    def interleave(connection, _cursor, statement, _params, _context, _many):
        if not fired and "radar_feature_observations" in statement and "radar_candidates" in statement and "JOIN" in statement:
            fired.append(True)
            connection.execute(ResearchState.__table__.update().where(ResearchState.chain == "bsc").values(epoch=2))

    event.listen(research_session.bind, "before_cursor_execute", interleave)
    try:
        assert projection_for(research_session, row) is None
        assert fired
    finally:
        event.remove(research_session.bind, "before_cursor_execute", interleave)


def test_postgres_supersession_waits_for_inflight_dependency_publication(research_session):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from sqlalchemy import event
    from sqlalchemy.orm import Session

    from smms.db.models import RadarCandidate, TokenRiskSnapshot
    from smms.db.repositories import TokenRiskSnapshotRepository
    from smms.research.lineage import manifest

    if research_session.bind.dialect.name != "postgresql":
        pytest.skip("source/dependent publication fences require PostgreSQL")
    row = candidate(research_session)
    row_id = row.id

    def risk(session, level):
        return TokenRiskSnapshotRepository(session).upsert(TokenRiskSnapshot(
            chain="bsc", token_address=TOKEN, as_of=CUTOFF, risk_version="risk-v1", source_epoch=0,
            overall_risk=level, contract_risk=level, liquidity_risk=level, holder_risk=level, behavior_risk=level,
            confidence="HIGH", input_manifest=manifest(kind="token_risk_snapshots", parameters={}, facts={"risk": level}),
        ))

    risk(research_session, "HIGH")
    research_session.commit()
    staged, release, updating, completed = Event(), Event(), Event(), Event()

    def parent():
        with Session(research_session.bind) as session:
            LiveSignalService(session).materialize(session.get(RadarCandidate, row_id), as_of=CUTOFF)
            staged.set()
            assert release.wait(timeout=10)
            session.commit()

    def source():
        with Session(research_session.bind) as session:
            risk(session, "LOW")
            session.commit()
            completed.set()

    def observe(_connection, _cursor, statement, _params, _context, _many):
        if statement.lstrip().upper().startswith("UPDATE") and "token_risk_snapshots" in statement:
            updating.set()

    event.listen(research_session.bind, "before_cursor_execute", observe)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            writer = pool.submit(parent)
            try:
                assert staged.wait(timeout=5)
                correction = pool.submit(source)
                assert updating.wait(timeout=5)
                assert not completed.wait(timeout=0.05)
            finally:
                release.set()
            writer.result(timeout=15)
            correction.result(timeout=15)
        assert candidate_view(research_session, row)["data_status"] == "REBUILDING"
        assert research_session.scalar(select(Signal)) is None
    finally:
        event.remove(research_session.bind, "before_cursor_execute", observe)


def test_dependency_invalidation_rolls_back_with_the_source_revision(research_session):
    from smms.db.models import TokenRiskSnapshot
    from smms.db.repositories import TokenRiskSnapshotRepository
    from smms.research.lineage import manifest

    row = candidate(research_session)

    def risk(level):
        return TokenRiskSnapshotRepository(research_session).upsert(TokenRiskSnapshot(
            chain="bsc", token_address=TOKEN, as_of=CUTOFF, risk_version="risk-v1", source_epoch=0,
            overall_risk=level, contract_risk=level, liquidity_risk=level, holder_risk=level, behavior_risk=level,
            confidence="HIGH", input_manifest=manifest(kind="token_risk_snapshots", parameters={}, facts={"risk": level}),
        ))

    risk("HIGH")
    original = LiveSignalService(research_session).materialize(row, as_of=CUTOFF)
    identity = original.id
    research_session.commit()
    risk("LOW")
    assert candidate_view(research_session, row)["data_status"] == "REBUILDING"
    research_session.rollback()
    restored = research_session.scalar(select(Signal))
    assert restored.id == identity and restored.risk_level == "HIGH"
    assert candidate_view(research_session, row)["data_status"] != "REBUILDING"


def test_token_admission_policy_does_not_version_the_shared_wallet_replay(research_session):
    from smms.db.models import WalletSmartMoneySnapshot
    from smms.smart_money.aggregation import IntelligenceAggregationService
    from smms.smart_money.early_buyers import EarlyBuyerConfig

    seed(research_session)
    fact(research_session)
    IntelligenceAggregationService(research_session, min_wallet_score=0).refresh(as_of=CUTOFF)
    identity = research_session.scalar(select(WalletSmartMoneySnapshot.id))
    IntelligenceAggregationService(research_session, min_wallet_score=60,
                                   early_buyer_config=EarlyBuyerConfig(top_n=1)).refresh(as_of=CUTOFF)
    assert research_session.scalar(select(WalletSmartMoneySnapshot.id)) == identity
    assert len(research_session.scalars(select(WalletSmartMoneySnapshot)
                                        .execution_options(include_invalid_snapshots=True)).all()) == 1


@pytest.mark.parametrize("future_receipt", [False, True])
@pytest.mark.parametrize("field", ["is_honeypot", "mintable"])
def test_historical_risk_reuses_only_available_immutable_provider_receipts(research_session, future_receipt, field):
    from smms.db.models import TokenRiskSnapshot
    from smms.external_evidence import EvidenceObservation
    from smms.external_evidence.orchestrator import ExternalEvidenceOrchestrator
    from smms.risk.aggregation import RiskAggregationService

    seed(research_session)
    fetched = CUTOFF + timedelta(seconds=1) if future_receipt else T0
    ExternalEvidenceOrchestrator(research_session, providers=[])._persist(EvidenceObservation(
        chain="bsc", subject_type="TOKEN", subject_address=TOKEN, field=field, value=True,
        source="goplus", evidence_level="TRUSTED_PROVIDER", observed_at=T0, fetched_at=fetched,
    ))
    RiskAggregationService(research_session).refresh(as_of=CUTOFF, token_address=TOKEN, historical_rebuild=True)
    risk = research_session.scalar(select(TokenRiskSnapshot))
    resolved = risk.input_manifest["facts"]["resolved_evidence"]
    assert (field in resolved) is (not future_receipt)
    if not future_receipt:
        assert resolved[field]["resolved_value"] is True
        if field == "mintable":
            assert risk.contract_risk != "UNKNOWN"

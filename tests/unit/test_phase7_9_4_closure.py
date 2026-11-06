from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from smms.api.app import _worker_health
from smms.db.models import (
    Base,
    EvidenceObservationRecord,
    RadarCandidate,
    Signal,
    Token,
    TokenRiskSnapshot,
    TokenSmartMoneySnapshot,
    TradeEvent,
    WalletSmartMoneySnapshot,
    WorkerHeartbeat,
)
from smms.external_evidence import Capability, EvidenceObservation
from smms.external_evidence.orchestrator import ExternalEvidenceOrchestrator
from smms.radar.audit import AuditQueue
from smms.radar.features import RadarFeatureSnapshot
from smms.radar.live_signal import LiveSignalService
from smms.risk.aggregation import RiskAggregationService
from smms.smart_money.aggregation import IntelligenceAggregationService

NOW = datetime(2026, 9, 12, 12, tzinfo=UTC)


def _session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return Session(engine)


def _candidate(session: Session, features: dict[str, object]) -> RadarCandidate:
    token = Token(chain="bsc", address="0xtoken", first_seen_at=NOW)
    session.add(token)
    session.flush()
    candidate = RadarCandidate(
        token_id=token.id,
        discovered_at=NOW,
        discovery_source="PAIR_CREATED",
        discovery_reason="CANONICAL_CHAIN_EVENT",
        latest_seen_at=NOW,
        features_json=features,
    )
    session.add(candidate)
    session.flush()
    return candidate


def test_audit_scheduler_uses_the_canonical_radar_feature_schema() -> None:
    smart_features = RadarFeatureSnapshot.unknown(
        age_seconds=0,
        market_phase="TRADING",
        known_smart_wallet_count=1,
    ).as_dict()
    with _session() as session:
        smart = AuditQueue(session, cooldown_seconds=0).schedule_candidate(
            _candidate(session, smart_features), now=NOW
        )
        assert smart.reason == "smart_money_entry"
        assert smart.priority == 100

    risk_features = RadarFeatureSnapshot.unknown(
        age_seconds=0,
        market_phase="TRADING",
        risk="HIGH",
    ).as_dict()
    with _session() as session:
        risk = AuditQueue(session, cooldown_seconds=0).schedule_candidate(
            _candidate(session, risk_features), now=NOW
        )
        assert risk.reason == "risk_deterioration"
        assert risk.priority == 95

    with pytest.raises(ValueError, match="missing radar feature keys"):
        RadarFeatureSnapshot.from_mapping({"risk": "LOW"})


def test_worker_health_distinguishes_missing_and_stale_heartbeats() -> None:
    with _session() as session:
        session.add(
            WorkerHeartbeat(
                instance_id="radar-1",
                worker_type="radar-worker",
                last_heartbeat_at=NOW.replace(hour=11, minute=58),
                status="RUNNING",
                details_json={},
            )
        )
        session.flush()
        health = _worker_health(session, now=NOW)
        assert health["radar_worker"]["status"] == "STALE"
        assert health["forward_worker"]["status"] == "DOWN"


def test_worker_health_uses_latest_instance_heartbeat() -> None:
    with _session() as session:
        session.add_all(
            [
                WorkerHeartbeat(
                    instance_id="current-radar",
                    worker_type="radar-worker",
                    last_heartbeat_at=NOW.replace(hour=11, minute=59),
                    status="RUNNING",
                    details_json={},
                ),
                WorkerHeartbeat(
                    instance_id="old-radar",
                    worker_type="radar-worker",
                    last_heartbeat_at=NOW.replace(hour=11, minute=0),
                    status="RUNNING",
                    details_json={},
                ),
            ]
        )
        session.flush()

        health = _worker_health(session, now=NOW)

        assert health["radar_worker"]["status"] == "HEALTHY"
        assert health["radar_worker"]["last_heartbeat_at"] == NOW.replace(
            hour=11, minute=59
        )


def test_incremental_intelligence_refresh_is_scoped_to_target_token_and_wallets() -> None:
    with _session() as session:
        session.add_all(
            [
                Token(chain="bsc", address="0xa", creation_timestamp=NOW),
                Token(chain="bsc", address="0xb", creation_timestamp=NOW),
                TradeEvent(
                    chain="bsc",
                    token_address="0xa",
                    trader_address="alice",
                    tx_hash="a-buy",
                    side="BUY",
                    timestamp=NOW,
                    source="fixture",
                    native_amount=1,
                ),
                TradeEvent(
                    chain="bsc",
                    token_address="0xb",
                    trader_address="bob",
                    tx_hash="b-buy",
                    side="BUY",
                    timestamp=NOW,
                    source="fixture",
                    native_amount=1,
                ),
            ]
        )
        session.flush()

        result = IntelligenceAggregationService(session, chain="bsc").refresh_token(
            "0xa", as_of=NOW
        )

        assert result.wallet_snapshots == 1
        assert result.token_snapshots == 1
        assert {row.wallet_address for row in session.query(WalletSmartMoneySnapshot)} == {
            "alice"
        }
        assert {row.token_address for row in session.query(TokenSmartMoneySnapshot)} == {"0xa"}


@pytest.mark.asyncio
async def test_external_evidence_orchestrator_persists_and_resolves_chain_first() -> None:
    class MarketProvider:
        provider_name = "dexscreener"
        capabilities = frozenset({Capability.TOKEN_MARKET})

        async def fetch_token_market(self, chain: str, token_address: str):
            return [
                EvidenceObservation(
                    chain=chain,
                    subject_type="TOKEN",
                    subject_address=token_address,
                    field="price_usd",
                    value="1.25",
                    source=self.provider_name,
                    evidence_level="TRUSTED_PROVIDER",
                    observed_at=NOW,
                    fetched_at=NOW,
                )
            ]

    class SecurityProvider:
        provider_name = "goplus"
        capabilities = frozenset({Capability.TOKEN_SECURITY})

        async def fetch_token_security(self, chain: str, token_address: str):
            return [
                EvidenceObservation(
                    chain=chain,
                    subject_type="TOKEN",
                    subject_address=token_address,
                    field="is_honeypot",
                    value=True,
                    source=self.provider_name,
                    evidence_level="TRUSTED_PROVIDER",
                    observed_at=NOW,
                    fetched_at=NOW,
                )
            ]

    chain_observation = EvidenceObservation(
        chain="bsc",
        subject_type="TOKEN",
        subject_address="0xtoken",
        field="is_honeypot",
        value=False,
        source="bsc_rpc",
        source_type="CHAIN",
        evidence_level="ON_CHAIN_VERIFIED",
        observed_at=NOW,
        fetched_at=NOW,
    )
    with _session() as session:
        resolved = await ExternalEvidenceOrchestrator(
            session, providers=[MarketProvider(), SecurityProvider()]
        ).refresh("bsc", "0xtoken", as_of=NOW, chain_observations=[chain_observation])

        assert resolved["price_usd"].resolved_value == "1.25"
        assert resolved["is_honeypot"].resolved_value is False
        assert resolved["is_honeypot"].conflict is True
        assert {row.source for row in session.query(EvidenceObservationRecord)} == {
            "bsc_rpc",
            "dexscreener",
            "goplus",
        }


@pytest.mark.asyncio
async def test_sync_orchestrator_seam_is_safe_inside_running_event_loop() -> None:
    chain_observation = EvidenceObservation(
        chain="bsc",
        subject_type="TOKEN",
        subject_address="0xtoken",
        field="is_honeypot",
        value=False,
        source="bsc_rpc",
        source_type="CHAIN",
        evidence_level="ON_CHAIN_VERIFIED",
        observed_at=NOW,
        fetched_at=NOW,
    )
    with _session() as session:
        resolved = ExternalEvidenceOrchestrator(session, providers=[]).refresh_sync(
            "bsc", "0xtoken", as_of=NOW, chain_observations=[chain_observation]
        )
        assert resolved["is_honeypot"].resolved_value is False
        assert session.query(EvidenceObservationRecord).count() == 1


def test_resolved_evidence_reaches_risk_and_live_signal_is_durable() -> None:
    features = RadarFeatureSnapshot.unknown(
        age_seconds=30,
        market_phase="TRADING",
        price="1",
        liquidity="100000",
        early_buyer_count=2,
        known_smart_wallet_count=3,
        smart_money_net_flow="5000",
        smart_money_score="100",
        risk="LOW",
        buy_count_5m=5,
        sell_count_5m=0,
    ).as_dict()
    with _session() as session:
        candidate = _candidate(session, features)
        token = session.get(Token, candidate.token_id)
        resolved = {
            "is_honeypot": {
                "resolved_value": False,
                "source": "goplus",
                "evidence_level": "TRUSTED_PROVIDER",
                "freshness": "FRESH",
                "confidence": 0.8,
                "conflict": True,
            }
        }
        RiskAggregationService(session, chain="bsc").refresh(
            as_of=NOW, token_address=token.address, resolved_evidence=resolved
        )
        risk = session.query(TokenRiskSnapshot).one()
        assert risk.dimensions_json["resolved_evidence"]["is_honeypot"]["conflict"] is True
        candidate.risk_snapshot_id = risk.id
        features["risk"] = risk.overall_risk
        candidate.features_json = features

        signal = LiveSignalService(session).materialize(candidate, as_of=NOW)

        assert signal.id is not None
        assert session.query(Signal).count() == 1
        assert candidate.latest_signal_id == signal.id
        assert signal.created_at == NOW
        assert signal.signal_type.value in {"STRONG", "INTERESTING", "WATCH", "HIGH_RISK"}


def test_risk_snapshot_serializes_decimal_dimension_details() -> None:
    """Numeric risk evidence must be JSON-safe for the PostgreSQL JSON column."""
    with _session() as session:
        row = Token(chain="bsc", address="0x" + "a" * 40, current_liquidity=Decimal(5000))
        session.add(row)
        session.flush()

        RiskAggregationService(session, chain="bsc").refresh(
            as_of=NOW, token_address=row.address,
            resolved_evidence={"liquidity_usd": {"resolved_value": Decimal(5000), "source": "fixture",
                                                "evidence_level": "TRUSTED_PROVIDER", "freshness": "FRESH",
                                                "confidence": 1}},
        )
        session.commit()

        risk = session.query(TokenRiskSnapshot).one()
        assert risk.dimensions_json["liquidity"]["details"]["current_liquidity"] == "5000"

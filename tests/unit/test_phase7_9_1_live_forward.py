from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from smms.db.models import Base, LiveSignalObservation, MarketEvent, Pair, Token
from smms.radar.live_forward import evaluate_due, performance

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_horizon_retry_and_t0_snapshot_are_immutable() -> None:
    with _session() as session:
        token = Token(address="t", chain="bsc")
        session.add(token)
        session.flush()
        session.add(Pair(chain_id=56, address="p", token0_address="t",
                         token1_address="quote", creation_timestamp=T0))
        original = {"price": "1", "price_basis": "USD", "pair_address": "p"}
        observation = LiveSignalObservation(
            candidate_id=1, token_id=token.id, token_address="t", observed_at=T0,
            signal_class="HIGH_SIGNAL", signal_score=80, price=Decimal(1),
            market_cap=100, liquidity=1000, feature_version="f1", config_version="c1",
            model_version="m1", sample_group="HIGH_SIGNAL", t0_snapshot_json=dict(original),
        )
        session.add(observation)
        session.add(MarketEvent(chain_id=56, event_type="SWAP", token_address="t", pair_address="p",
                                tx_hash="x", timestamp=T0 + timedelta(seconds=30), price=2,
                                source="fixture", metadata_json={"price_basis": "USD"}))
        session.commit()
        evaluate_due(session, at=T0 + timedelta(seconds=30))
        session.commit()
        assert session.get(LiveSignalObservation, observation.id).t0_snapshot_json == original
        assert session.query(LiveSignalObservation).count() == 1
        summary = performance(session, at=T0 + timedelta(seconds=30))
        assert summary["mode"] == "FORWARD_OBSERVATION"
        assert summary["horizons"]["30s"]["HIGH_SIGNAL"]["sample_count"] == 1

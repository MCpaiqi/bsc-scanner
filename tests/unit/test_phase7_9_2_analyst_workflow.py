from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from smms.db.models import AlertHistory, AlertNotification, Base, RadarAnnotation
from smms.radar.alerts import (
    InvalidRuleError,
    create_rule,
    evaluate_and_notify,
    evaluate_conditions,
    evaluate_rule,
)


def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_rule_evaluation_supports_safe_composition_and_unknown_is_rejected():
    rule = {
        "name": "smart money",
        "conditions": [
            {"field": "smart_money_score", "operator": ">=", "value": 80},
            {"field": "smart_wallet_count", "operator": ">=", "value": 3},
            {"field": "risk", "operator": "!=", "value": "HIGH"},
            {"field": "liquidity", "operator": ">=", "value": 20_000},
        ],
    }
    assert evaluate_rule(
        rule, {"smart_money_score": 90, "smart_wallet_count": 4, "risk": "LOW", "liquidity": 25_000}
    )
    assert not evaluate_conditions(
        rule["conditions"],
        {"smart_money_score": 90, "smart_wallet_count": 4, "risk": "HIGH", "liquidity": 25_000},
    )
    with pytest.raises(InvalidRuleError, match="UNKNOWN field"):
        evaluate_rule({"conditions": [{"field": "python", "operator": "==", "value": 1}]}, {})


def test_annotation_crud_is_separate_from_canonical_models():
    with session() as db:
        row = RadarAnnotation(
            chain="solana", token_address="mint", favorite=True, note="watch", tags=["early"]
        )
        db.add(row)
        db.commit()
        loaded = db.query(RadarAnnotation).one()
        loaded.note = "updated"
        db.commit()
        assert loaded.favorite is True and loaded.tags == ["early"] and loaded.note == "updated"
        db.delete(loaded)
        db.commit()
        assert db.query(RadarAnnotation).count() == 0


def test_dedup_and_cooldown_prevent_repeated_notifications():
    now = datetime(2026, 9, 12, tzinfo=UTC)
    with session() as db:
        rule = create_rule(
            db,
            {
                "name": "liquidity",
                "conditions": [{"field": "liquidity", "operator": ">=", "value": 20_000}],
                "cooldown_seconds": 60,
            },
        )
        first = evaluate_and_notify(
            db, rule, chain="solana", token_address="mint", state={"liquidity": 25_000}, at=now
        )
        again = evaluate_and_notify(
            db,
            rule,
            chain="solana",
            token_address="mint",
            state={"liquidity": 25_000},
            at=now + timedelta(seconds=10),
        )
        after = evaluate_and_notify(
            db,
            rule,
            chain="solana",
            token_address="mint",
            state={"liquidity": 25_000},
            at=now + timedelta(seconds=61),
        )
        assert first["notified"] is True
        assert again["reason"] == "COOLDOWN"
        assert after["reason"] == "DEDUP"
        assert db.query(AlertHistory).count() == 1
        assert db.query(AlertNotification).count() == 1

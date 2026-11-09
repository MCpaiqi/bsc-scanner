"""Typed alert operands and isolation of incompatible retained legacy rules."""

from contextlib import asynccontextmanager
from decimal import Decimal

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from test_alpha_collector import coverage_session as _coverage_session
from test_research_integrity import research_session as _research_session

from smms.api import analyst
from smms.config.settings import Settings, get_settings
from smms.db.models import AlertHistory, AlertNotification, AlertRule
from smms.radar.alerts import (
    InvalidRuleError,
    create_rule,
    evaluate_and_notify,
    evaluate_conditions,
    evaluate_enabled_rules,
    validate_conditions,
)

research_session = _research_session
coverage_session = _coverage_session
TOKEN = "0x" + "a1" * 20
KEY = "synthetic-operand-credential-" + "a" * 32


def condition(field="smart_money_score", operator=">=", value=60):
    return {"field": field, "operator": operator, "value": value}


BAD = [
    condition(value={}), condition(value=[]), condition(value=True), condition(value=None),
    condition(value="60"), condition(value=float("nan")), condition(value=float("inf")),
    condition(value=-1), condition(value=101), condition(field=[]), condition(operator={}),
    condition(field="smart_wallet_count", value=1.5), condition(field="liquidity", value=-1),
    condition(field="risk", value=60), condition(field="risk", operator=">", value="HIGH"),
    condition(field="risk", operator="==", value="bad"), condition(operator="in", value=[60, {}]),
    condition(field="risk", operator="in", value=["HIGH", 60]),
]


@pytest.mark.parametrize("item", BAD)
def test_invalid_operands_are_rejected_at_the_shared_validation_boundary(item):
    with pytest.raises(InvalidRuleError):
        validate_conditions([item])


@pytest.mark.parametrize("actual", [{}, [], True, "NaN", float("inf"), -1, 101, None, "UNKNOWN"])
def test_invalid_observations_cannot_trigger_inequality_or_crash(actual):
    assert not evaluate_conditions([condition(operator="!=")], {"smart_money_score": actual})


def test_numeric_membership_handles_serialized_observations_and_known_risk():
    assert evaluate_conditions([condition(operator="in", value=[60, 80]),
                                condition(field="risk", operator="in", value=["LOW", "MEDIUM"])],
                               {"smart_money_score": "80", "risk": "LOW"})
    assert not evaluate_conditions([condition(field="risk", operator="!=", value="HIGH")], {"risk": {}})
    assert not evaluate_conditions([condition(field="risk", operator="==", value="UNKNOWN")], {"risk": "UNKNOWN"})


def test_bad_legacy_rule_is_retained_visible_and_cannot_interrupt_other_rules(research_session):
    legacy = AlertRule(name="legacy fixture", conditions_json=[condition(value={})], match_mode="all",
                       notification_channels=["in-app"], cooldown_seconds=0, enabled=True, chain="bsc")
    research_session.add(legacy)
    research_session.flush()
    valid = create_rule(research_session, {"name": "valid fixture", "chain": "bsc", "conditions": [condition()]})
    research_session.commit()
    state = {"smart_money_score": 90}
    results = evaluate_enabled_rules(research_session, chain="bsc", token_address=TOKEN, state=state)
    assert [item["rule_id"] for item in results] == [valid.id]
    assert evaluate_and_notify(research_session, legacy, chain="bsc", token_address=TOKEN, state=state) == {
        "matched": False, "notified": False, "reason": "INVALID_RULE"}
    assert research_session.scalar(select(func.count()).select_from(AlertHistory)) == 1
    assert research_session.scalar(select(func.count()).select_from(AlertNotification)) == 1
    assert legacy.enabled and legacy.conditions_json == [condition(value={})]
    assert analyst._rule_payload(legacy)["validation_status"] == "INVALID"
    assert analyst._rule_payload(valid)["validation_status"] == "VALID"


def test_partial_matching_state_is_json_safe_and_preserves_unknown(research_session):
    rule = create_rule(research_session, {"name": "partial fixture", "match_mode": "any", "conditions": [
        condition(), condition(field="liquidity", value=10)]})
    result = evaluate_and_notify(research_session, rule, chain="bsc", token_address=TOKEN,
                                 state={"smart_money_score": Decimal(90), "liquidity": float("nan")})
    assert result["notified"]
    history = research_session.get(AlertHistory, result["history_id"])
    assert history.state_json["values"] == {"smart_money_score": "90", "liquidity": None}


@pytest.mark.asyncio
async def test_authenticated_invalid_create_and_patch_preserve_existing_rules(coverage_session, monkeypatch):
    @asynccontextmanager
    async def scope(_settings):
        yield coverage_session
    monkeypatch.setattr(analyst, "session_scope", scope)
    app = FastAPI()
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, SMMS_ANALYST_KEY=KEY)
    app.include_router(analyst.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           headers={"Authorization": "Bearer " + KEY}) as client:
        for item in (condition(value={}), condition(field=[]), condition(operator={}), condition(value=True)):
            response = await client.post("/api/radar/alert-rules", json={"name": "invalid fixture", "conditions": [item]})
            assert response.status_code == 422
        assert await coverage_session.scalar(select(func.count()).select_from(AlertRule)) == 0
        created = await client.post("/api/radar/alert-rules", json={"name": "valid fixture", "conditions": [condition()]})
        assert created.status_code == 201
        path = "/api/radar/alert-rules/" + str(created.json()["id"])
        assert (await client.patch(path, json={"conditions": [condition(value={})]})).status_code == 422
        result = (await client.get(path)).json()
        assert result["conditions"] == [condition()] and result["validation_status"] == "VALID"

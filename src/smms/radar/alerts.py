"""Safe declarative analyst alerts and notification delivery."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from smms.db.models import AlertHistory, AlertNotification, AlertRule, RadarCandidate, Token
from smms.evaluation import utc

ALLOWED_FIELDS = {
    "smart_money_score": "smart_money_score",
    "smart_wallet_count": "smart_wallet_count",
    "risk": "risk",
    "liquidity": "liquidity",
}
ALLOWED_OPERATORS = {"==", "!=", ">", ">=", "<", "<=", "in"}
ALLOWED_CHANNELS = {"in-app", "browser", "webhook"}


class InvalidRuleError(ValueError):
    pass


def _condition_parts(condition: Mapping[str, Any]) -> tuple[str, str, Any]:
    field = condition.get("field")
    operator = condition.get("operator", condition.get("op"))
    if field not in ALLOWED_FIELDS:
        raise InvalidRuleError(f"UNKNOWN field: {field}")
    if operator not in ALLOWED_OPERATORS:
        raise InvalidRuleError(f"invalid operator: {operator}")
    if "value" not in condition:
        raise InvalidRuleError("condition value is required")
    value = condition["value"]
    if operator == "in" and not isinstance(value, (list, tuple, set)):
        raise InvalidRuleError("in operator requires a list value")
    if operator == "in":
        value = list(value)
    return field, str(operator), value


def validate_conditions(conditions: Any, *, match_mode: str = "all") -> list[dict[str, Any]]:
    if not isinstance(conditions, list) or not conditions:
        raise InvalidRuleError("at least one condition is required")
    if match_mode not in {"all", "any"}:
        raise InvalidRuleError("match_mode must be all or any")
    normalized: list[dict[str, Any]] = []
    for item in conditions:
        if not isinstance(item, Mapping):
            raise InvalidRuleError("conditions must be objects")
        field, operator, value = _condition_parts(item)
        normalized.append({"field": field, "operator": operator, "value": value})
    return normalized


def _state_value(state: Mapping[str, Any], field: str) -> Any:
    aliases = {
        "risk": ("risk", "overall_risk"),
        "liquidity": ("liquidity", "current_liquidity"),
        "smart_money_score": ("smart_money_score",),
        "smart_wallet_count": ("smart_wallet_count", "known_smart_wallet_count"),
    }
    for key in aliases[field]:
        if key in state:
            return state[key]
    return None


def _compare(actual: Any, operator: str, expected: Any) -> bool:
    if actual is None or actual == "UNKNOWN":
        return False
    if operator == "in":
        return actual in expected
    if isinstance(expected, (int, float, Decimal)) and not isinstance(expected, bool):
        try:
            actual, expected = Decimal(str(actual)), Decimal(str(expected))
        except (InvalidOperation, ValueError):
            return False
    if operator == "==":
        return actual == expected
    if operator == "!=":
        return actual != expected
    if operator == ">":
        return actual > expected
    if operator == ">=":
        return actual >= expected
    if operator == "<":
        return actual < expected
    return actual <= expected


def evaluate_conditions(
    conditions: Any, state: Mapping[str, Any], *, match_mode: str = "all"
) -> bool:
    normalized = validate_conditions(conditions, match_mode=match_mode)
    results = [
        _compare(_state_value(state, item["field"]), item["operator"], item["value"])
        for item in normalized
    ]
    return all(results) if match_mode == "all" else any(results)


def _rule_conditions(rule: AlertRule | Mapping[str, Any]) -> tuple[list[dict[str, Any]], str]:
    if isinstance(rule, AlertRule):
        return validate_conditions(
            rule.conditions_json, match_mode=rule.match_mode
        ), rule.match_mode
    mode = str(rule.get("match_mode", "all"))
    return validate_conditions(
        rule.get("conditions", rule.get("conditions_json")), match_mode=mode
    ), mode


def evaluate_rule(rule: AlertRule | Mapping[str, Any], state: Mapping[str, Any]) -> bool:
    conditions, mode = _rule_conditions(rule)
    return evaluate_conditions(conditions, state, match_mode=mode)


def state_key(
    rule: AlertRule | Mapping[str, Any], state: Mapping[str, Any]
) -> tuple[str, dict[str, Any]]:
    conditions, mode = _rule_conditions(rule)
    relevant = {
        "mode": mode,
        "matched": evaluate_conditions(conditions, state, match_mode=mode),
        "values": {item["field"]: _state_value(state, item["field"]) for item in conditions},
    }
    encoded = json.dumps(relevant, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest(), relevant


def validate_rule_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    name = str(payload.get("name", "")).strip()
    if not name:
        raise InvalidRuleError("name is required")
    mode = str(payload.get("match_mode", "all"))
    conditions = validate_conditions(
        payload.get("conditions", payload.get("conditions_json")), match_mode=mode
    )
    channels = payload.get("notification_channels", ["in-app"])
    if (
        not isinstance(channels, list)
        or not channels
        or any(channel not in ALLOWED_CHANNELS for channel in channels)
    ):
        raise InvalidRuleError(f"notification_channels must use {sorted(ALLOWED_CHANNELS)}")
    cooldown = int(payload.get("cooldown_seconds", 300))
    if cooldown < 0:
        raise InvalidRuleError("cooldown_seconds must be >= 0")
    return {
        "name": name,
        "chain": payload.get("chain"),
        "conditions_json": conditions,
        "match_mode": mode,
        "notification_channels": channels,
        "cooldown_seconds": cooldown,
        "enabled": bool(payload.get("enabled", True)),
    }


def create_rule(session: Session, payload: Mapping[str, Any]) -> AlertRule:
    values = validate_rule_payload(payload)
    rule = AlertRule(**values)
    session.add(rule)
    session.flush()
    return rule


def evaluate_enabled_rules(
    session: Session,
    *,
    chain: str,
    token_address: str,
    state: Mapping[str, Any],
    at: datetime | None = None,
) -> list[dict[str, Any]]:
    from smms.research.state import capture_epoch

    from .projection import projection_for

    candidate = session.scalar(select(RadarCandidate).join(Token, Token.id == RadarCandidate.token_id).where(
        Token.chain == chain, Token.address == token_address,
    ))
    projection = projection_for(session, candidate) if candidate else None
    if projection is not None:
        if projection.snapshot_status != "VALID" or utc(projection.as_of) > utc(at or datetime.now(UTC)):
            return []
        state = projection.features_json
    elif capture_epoch(session, chain) > 0 or (candidate and candidate.projection_id is not None):
        return []
    results = []
    for rule in session.scalars(select(AlertRule).where(AlertRule.enabled.is_(True))).all():
        result = evaluate_and_notify(
            session, rule, chain=chain, token_address=token_address, state=state, at=at
        )
        if result.get("notified"):
            results.append({"rule_id": rule.id, **result})
    return results


def evaluate_and_notify(
    session: Session,
    rule: AlertRule,
    *,
    chain: str,
    token_address: str,
    state: Mapping[str, Any],
    at: datetime | None = None,
) -> dict[str, Any]:
    now = at or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    if not rule.enabled or (rule.chain and rule.chain != chain):
        return {"matched": False, "notified": False, "reason": "DISABLED_OR_CHAIN"}
    matched = evaluate_rule(rule, state)
    if not matched:
        return {"matched": False, "notified": False, "reason": "NO_MATCH"}
    key, snapshot = state_key(rule, state)
    previous = session.scalar(
        select(AlertHistory)
        .where(
            AlertHistory.rule_id == rule.id,
            AlertHistory.chain == chain,
            AlertHistory.token_address == token_address,
            AlertHistory.notified_at.is_not(None),
        )
        .order_by(AlertHistory.notified_at.desc())
    )
    previous_at = (
        previous.notified_at.replace(tzinfo=UTC)
        if previous is not None and previous.notified_at.tzinfo is None
        else (previous.notified_at if previous is not None else None)
    )
    if previous is not None and (now - previous_at).total_seconds() < rule.cooldown_seconds:
        return {"matched": True, "notified": False, "reason": "COOLDOWN", "history_id": previous.id}
    if previous is not None and previous.state_key == key:
        return {"matched": True, "notified": False, "reason": "DEDUP", "history_id": previous.id}
    history = AlertHistory(
        rule_id=rule.id,
        chain=chain,
        token_address=token_address,
        state_key=key,
        state_json=snapshot,
        matched=True,
        evaluated_at=now,
        notified_at=now,
    )
    session.add(history)
    session.flush()
    payload = {
        "rule_id": rule.id,
        "rule_name": rule.name,
        "chain": chain,
        "token_address": token_address,
        "state": snapshot,
        "occurred_at": now.isoformat(),
    }
    for channel in rule.notification_channels:
        status = "DELIVERED" if channel in {"in-app", "browser"} else "ADAPTER_REQUIRED"
        session.add(
            AlertNotification(
                history_id=history.id,
                channel=channel,
                status=status,
                payload_json=payload,
                delivered_at=now,
            )
        )
    session.flush()
    return {
        "matched": True,
        "notified": True,
        "reason": "TRIGGERED",
        "history_id": history.id,
        "channels": rule.notification_channels,
    }

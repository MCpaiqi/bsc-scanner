"""Safe declarative analyst alerts and notification delivery."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from smms.chains.constants import normalize_bsc_address, validate_bsc_chain
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
RISK_LEVELS = {"LOW", "MEDIUM", "HIGH", "CRITICAL", "UNKNOWN"}


class InvalidRuleError(ValueError):
    pass


def _number(value: Any, field: str, *, observation: bool = False) -> Decimal | None:
    types = (int, float, Decimal, str) if observation else (int, float)
    if isinstance(value, bool) or not isinstance(value, types):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite() or number < 0:
        return None
    if field == "smart_money_score" and number > 100:
        return None
    if field == "smart_wallet_count" and number != number.to_integral_value():
        return None
    return number


def _operand(value: Any, field: str) -> Any:
    if field == "risk":
        if not isinstance(value, str) or value not in RISK_LEVELS:
            raise InvalidRuleError("risk operands must be known risk levels")
    elif _number(value, field) is None:
        raise InvalidRuleError("numeric operands must be finite nonnegative JSON numbers; score <=100 and wallet count integral")
    return value


def _condition_parts(condition: Mapping[str, Any]) -> tuple[str, str, Any]:
    field = condition.get("field")
    operator = condition.get("operator", condition.get("op"))
    if not isinstance(field, str) or field not in ALLOWED_FIELDS:
        raise InvalidRuleError("UNKNOWN field")
    if not isinstance(operator, str) or operator not in ALLOWED_OPERATORS:
        raise InvalidRuleError("invalid operator")
    if field == "risk" and operator not in {"==", "!=", "in"}:
        raise InvalidRuleError("risk only supports equality and membership operators")
    if "value" not in condition:
        raise InvalidRuleError("condition value is required")
    value = condition["value"]
    if operator == "in" and not isinstance(value, (list, tuple, set)):
        raise InvalidRuleError("in operator requires a list value")
    if operator == "in":
        value = [_operand(item, field) for item in value]
    else:
        value = _operand(value, field)
    return field, str(operator), value


def validate_conditions(conditions: Any, *, match_mode: str = "all") -> list[dict[str, Any]]:
    if not isinstance(conditions, list) or not conditions:
        raise InvalidRuleError("at least one condition is required")
    if not isinstance(match_mode, str) or match_mode not in {"all", "any"}:
        raise InvalidRuleError("match_mode must be all or any")
    normalized: list[dict[str, Any]] = []
    for item in conditions:
        if not isinstance(item, Mapping):
            raise InvalidRuleError("conditions must be objects")
        field, operator, value = _condition_parts(item)
        normalized.append({"field": field, "operator": operator, "value": value})
    return normalized


def _state_value(state: Mapping[str, Any], field: str) -> Any:
    if not isinstance(state, Mapping):
        return None
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


def _compare(field: str, actual: Any, operator: str, expected: Any) -> bool:
    if actual is None or actual == "UNKNOWN":
        return False
    if field == "risk":
        if not isinstance(actual, str) or actual not in RISK_LEVELS:
            return False
    else:
        actual = _number(actual, field, observation=True)
        if actual is None:
            return False
        expected = ([_number(item, field) for item in expected] if operator == "in"
                    else _number(expected, field))
    if operator == "in":
        return actual in expected
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
        _compare(item["field"], _state_value(state, item["field"]), item["operator"], item["value"])
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
    def snapshot_value(field):
        value = _state_value(state, field)
        if value is None or value == "UNKNOWN":
            return value
        valid = (isinstance(value, str) and value in RISK_LEVELS if field == "risk"
                 else _number(value, field, observation=True) is not None)
        if not valid:
            return None
        return str(value) if isinstance(value, Decimal) else value
    relevant = {
        "mode": mode,
        "matched": evaluate_conditions(conditions, state, match_mode=mode),
        "values": {item["field"]: snapshot_value(item["field"]) for item in conditions},
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
    target = payload.get("token_address")
    scope = payload.get("scope", "TOKEN" if target is not None else "GLOBAL")
    chain = payload.get("chain")
    if scope not in {"GLOBAL", "TOKEN"} or (scope == "GLOBAL" and target is not None):
        raise InvalidRuleError("scope and token_address contradict each other")
    if scope == "TOKEN":
        try:
            chain, target = validate_bsc_chain(chain), normalize_bsc_address(target)
        except ValueError as exc:
            raise InvalidRuleError(str(exc)) from exc
    request_id = payload.get("request_id")
    if request_id is not None:
        try:
            request_id = str(UUID(str(request_id)))
        except (ValueError, AttributeError) as exc:
            raise InvalidRuleError("invalid request_id") from exc
    return {
        "name": name,
        "chain": chain,
        "token_address": target,
        "request_id": request_id,
        "conditions_json": conditions,
        "match_mode": mode,
        "notification_channels": channels,
        "cooldown_seconds": cooldown,
        "enabled": bool(payload.get("enabled", True)),
    }


def create_rule(session: Session, payload: Mapping[str, Any]) -> AlertRule:
    values = validate_rule_payload(payload)
    def existing():
        row = session.scalar(select(AlertRule).where(AlertRule.request_id == values["request_id"])
                             .execution_options(populate_existing=True))
        if row is not None and row.deleted:
            raise InvalidRuleError("request_id belongs to a deleted rule")
        if row is not None and any(getattr(row, key) != value for key, value in values.items()):
            raise InvalidRuleError("request_id already belongs to a different rule payload")
        return row
    if values["request_id"] is not None and (row := existing()) is not None:
        return row
    rule = AlertRule(**values)
    try:
        with session.begin_nested():
            session.add(rule)
            session.flush()
    except IntegrityError:
        if values["request_id"] is not None and (row := existing()) is not None:
            return row
        raise
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
    for rule in session.scalars(select(AlertRule).where(AlertRule.enabled.is_(True), AlertRule.deleted.is_(False),
        or_(AlertRule.chain.is_(None), AlertRule.chain == chain),
        or_(AlertRule.token_address.is_(None), AlertRule.token_address == (token_address.lower() if chain == "bsc" else token_address)),
    )).all():
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
    if rule.deleted or not rule.enabled or (rule.chain and rule.chain != chain):
        return {"matched": False, "notified": False, "reason": "DISABLED_OR_CHAIN"}
    token_address = token_address.lower() if chain == "bsc" else token_address
    if rule.token_address is not None and rule.token_address != token_address:
        return {"matched": False, "notified": False, "reason": "OUT_OF_SCOPE"}
    try:
        matched = evaluate_rule(rule, state)
    except InvalidRuleError:
        # Retain incompatible legacy rules for repair, without stopping other rules.
        return {"matched": False, "notified": False, "reason": "INVALID_RULE"}
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
        "rule_scope": rule.scope,
        "rule_token_address": rule.token_address,
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

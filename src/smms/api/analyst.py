"""Analyst workflow API: annotations and safe alert rules."""

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from smms.chains.constants import normalize_bsc_address, validate_bsc_chain
from smms.config.settings import get_settings
from smms.db.database import session_scope
from smms.db.models import AlertHistory, AlertRule, RadarAnnotation
from smms.radar.alerts import (
    InvalidRuleError,
    create_rule,
    evaluate_and_notify,
    validate_rule_payload,
)
from smms.radar.hooks import enqueue_alert

router = APIRouter(prefix="/api/radar", tags=["analyst-workflow"])


def _chain(value: str | None) -> str:
    try:
        return validate_bsc_chain(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _address(value: str) -> str:
    try:
        return normalize_bsc_address(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _validated_rule_payload(payload: "RulePayload") -> dict[str, Any]:
    """Apply the active BSC boundary before persisting an alert rule."""

    values = payload.model_dump()
    values["chain"] = _chain(payload.chain)
    try:
        return validate_rule_payload(values)
    except (InvalidRuleError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


class AnnotationPayload(BaseModel):
    favorite: bool = False
    note: str | None = None
    tags: list[str] = Field(default_factory=list)


class RulePayload(BaseModel):
    name: str
    chain: str | None = None
    conditions: list[dict[str, Any]]
    match_mode: str = "all"
    notification_channels: list[str] = Field(default_factory=lambda: ["in-app"])
    cooldown_seconds: int = Field(300, ge=0)
    enabled: bool = True


class RuleEvaluationPayload(BaseModel):
    chain: str
    token_address: str
    state: dict[str, Any]


def _annotation_payload(row: RadarAnnotation) -> dict[str, Any]:
    return {
        "id": row.id,
        "chain": row.chain,
        "token_address": row.token_address,
        "favorite": row.favorite,
        "note": row.note,
        "tags": row.tags,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _rule_payload(row: AlertRule) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "chain": row.chain,
        "conditions": row.conditions_json,
        "match_mode": row.match_mode,
        "notification_channels": row.notification_channels,
        "cooldown_seconds": row.cooldown_seconds,
        "enabled": row.enabled,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


@router.get("/annotations")
async def list_annotations(chain: str | None = None, favorite: bool | None = None):
    chain = _chain(chain)
    async with session_scope(get_settings()) as session:

        def read(sync):
            query = select(RadarAnnotation).order_by(RadarAnnotation.updated_at.desc())
            if chain:
                query = query.where(RadarAnnotation.chain == chain)
            if favorite is not None:
                query = query.where(RadarAnnotation.favorite.is_(favorite))
            return [_annotation_payload(row) for row in sync.scalars(query).all()]

        return {"annotations": await session.run_sync(read)}


@router.put("/annotations/{chain}/{token_address}")
async def upsert_annotation(chain: str, token_address: str, payload: AnnotationPayload):
    chain, token_address = _chain(chain), _address(token_address)
    async with session_scope(get_settings()) as session:

        def write(sync):
            row = sync.scalar(
                select(RadarAnnotation).where(
                    RadarAnnotation.chain == chain, RadarAnnotation.token_address == token_address
                )
            )
            if row is None:
                row = RadarAnnotation(chain=chain, token_address=token_address)
                sync.add(row)
            row.favorite, row.note = payload.favorite, payload.note
            row.tags = sorted({str(tag).strip() for tag in payload.tags if str(tag).strip()})
            row.updated_at = datetime.now(UTC)
            sync.flush()
            return _annotation_payload(row)

        result = await session.run_sync(write)
        await session.commit()
        return result


@router.get("/annotations/{chain}/{token_address}")
async def get_annotation(chain: str, token_address: str):
    chain, token_address = _chain(chain), _address(token_address)
    async with session_scope(get_settings()) as session:
        row = await session.scalar(
            select(RadarAnnotation).where(
                RadarAnnotation.chain == chain, RadarAnnotation.token_address == token_address
            )
        )
    if row is None:
        raise HTTPException(404, "annotation not found")
    return _annotation_payload(row)


@router.delete("/annotations/{chain}/{token_address}")
async def delete_annotation(chain: str, token_address: str):
    chain, token_address = _chain(chain), _address(token_address)
    async with session_scope(get_settings()) as session:
        row = await session.scalar(
            select(RadarAnnotation).where(
                RadarAnnotation.chain == chain, RadarAnnotation.token_address == token_address
            )
        )
        if row is None:
            raise HTTPException(404, "annotation not found")
        await session.delete(row)
        await session.commit()
        return {"deleted": True}


@router.get("/alert-rules")
async def list_alert_rules(enabled: bool | None = None):
    async with session_scope(get_settings()) as session:

        def read(sync):
            query = select(AlertRule).order_by(AlertRule.id)
            if enabled is not None:
                query = query.where(AlertRule.enabled.is_(enabled))
            return [_rule_payload(row) for row in sync.scalars(query).all()]

        return {"rules": await session.run_sync(read)}


@router.post("/alert-rules", status_code=201)
async def add_alert_rule(payload: RulePayload):
    values = _validated_rule_payload(payload)
    async with session_scope(get_settings()) as session:
        row = await session.run_sync(lambda sync: create_rule(sync, values))
        result = _rule_payload(row)
        await session.commit()
        return result


@router.get("/alert-rules/{rule_id}")
async def get_alert_rule(rule_id: int):
    async with session_scope(get_settings()) as session:
        row = await session.get(AlertRule, rule_id)
    if row is None:
        raise HTTPException(404, "alert rule not found")
    return _rule_payload(row)


@router.patch("/alert-rules/{rule_id}")
async def update_alert_rule(rule_id: int, payload: RulePayload):
    values = _validated_rule_payload(payload)
    async with session_scope(get_settings()) as session:
        row = await session.get(AlertRule, rule_id)
        if row is None:
            raise HTTPException(404, "alert rule not found")
        for key, value in values.items():
            setattr(row, key, value)
        row.updated_at = datetime.now(UTC)
        await session.flush()
        result = _rule_payload(row)
        await session.commit()
        return result


@router.delete("/alert-rules/{rule_id}")
async def delete_alert_rule(rule_id: int):
    async with session_scope(get_settings()) as session:
        row = await session.get(AlertRule, rule_id)
        if row is None:
            raise HTTPException(404, "alert rule not found")
        await session.delete(row)
        await session.commit()
        return {"deleted": True}


@router.post("/alert-rules/{rule_id}/evaluate")
async def evaluate_alert_rule(rule_id: int, payload: RuleEvaluationPayload):
    chain, token_address = _chain(payload.chain), _address(payload.token_address)
    async with session_scope(get_settings()) as session:
        row = await session.get(AlertRule, rule_id)
        if row is None:
            raise HTTPException(404, "alert rule not found")
        result = await session.run_sync(
            lambda sync: evaluate_and_notify(
                sync,
                row,
                chain=chain,
                token_address=token_address,
                state=payload.state,
            )
        )
        if result.get("notified") and "browser" in row.notification_channels:
            await session.run_sync(lambda sync: enqueue_alert(
                sync, chain=chain, token=token_address, alert={"rule_id": row.id, **result},
            ))
        await session.commit()
        return result


@router.get("/alert-history")
async def list_alert_history(
    chain: str | None = None,
    token_address: str | None = None,
    limit: int = Query(100, ge=1, le=500),
):
    chain = _chain(chain)
    if token_address:
        token_address = _address(token_address)
    async with session_scope(get_settings()) as session:

        def read(sync):
            query = select(AlertHistory).order_by(AlertHistory.evaluated_at.desc()).limit(limit)
            if chain:
                query = query.where(AlertHistory.chain == chain)
            if token_address:
                query = query.where(AlertHistory.token_address == token_address)
            return [
                {
                    "id": row.id,
                    "rule_id": row.rule_id,
                    "chain": row.chain,
                    "token_address": row.token_address,
                    "state": row.state_json,
                    "evaluated_at": row.evaluated_at,
                    "notified_at": row.notified_at,
                }
                for row in sync.scalars(query).all()
            ]

        return {"history": await session.run_sync(read)}

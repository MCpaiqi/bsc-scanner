"""Analyst writes and rule targets must be explicit and retry-safe."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from test_alpha_collector import coverage_session as _coverage_session
from test_research_integrity import research_session as _research_session

from smms.db.models import AlertHistory, AlertRule, RadarAnnotation
from smms.radar.alerts import (
    InvalidRuleError,
    create_rule,
    evaluate_and_notify,
    evaluate_enabled_rules,
)

research_session = _research_session
coverage_session = _coverage_session
TOKEN = "0x" + "a1" * 20
OTHER = "0x" + "2" * 40
NOW = datetime(2026, 11, 7, tzinfo=UTC)


def payload(**kwargs):
    return {"name": "fixture", "chain": "bsc", "conditions": [
        {"field": "smart_money_score", "operator": ">=", "value": 60}], **kwargs}


def test_token_rule_guards_automatic_and_explicit_evaluation(research_session):
    rule = create_rule(research_session, payload(scope="TOKEN", token_address=TOKEN))
    state = {"smart_money_score": 90}
    assert not evaluate_and_notify(research_session, rule, chain="bsc", token_address=OTHER, state=state, at=NOW)["notified"]
    assert evaluate_enabled_rules(research_session, chain="bsc", token_address=OTHER, state=state, at=NOW) == []
    assert research_session.scalar(select(func.count()).select_from(AlertHistory)) == 0
    assert evaluate_and_notify(research_session, rule, chain="bsc", token_address=TOKEN.upper().replace("0X", "0x"), state=state, at=NOW)["notified"]
    assert rule.scope == "TOKEN" and rule.token_address == TOKEN


def test_explicit_global_and_legacy_global_rules_keep_their_scope(research_session):
    for data in (payload(scope="GLOBAL"), payload()):
        rule = create_rule(research_session, data)
        assert rule.scope == "GLOBAL" and rule.token_address is None
        for token in [TOKEN, OTHER]:
            assert evaluate_and_notify(research_session, rule, chain="bsc", token_address=token,
                state={"smart_money_score": 90}, at=NOW)["notified"]


@pytest.mark.parametrize("extra", [
    {"scope": "TOKEN"}, {"scope": "TOKEN", "token_address": "bad"},
    {"scope": "TOKEN", "chain": "solana", "token_address": TOKEN},
    {"scope": "GLOBAL", "token_address": TOKEN}, {"scope": "wrong"},
])
def test_invalid_or_contradictory_scope_is_rejected(research_session, extra):
    with pytest.raises(InvalidRuleError):
        create_rule(research_session, payload(**extra))


def test_alert_creation_retries_are_idempotent_and_payload_bound(research_session):
    data = payload(scope="TOKEN", token_address=TOKEN, request_id=str(uuid4()))
    first = create_rule(research_session, data)
    again = create_rule(research_session, data)
    assert first.id == again.id
    assert research_session.scalar(select(func.count()).select_from(AlertRule)) == 1
    with pytest.raises(InvalidRuleError, match="request_id"):
        create_rule(research_session, {**data, "token_address": OTHER})


@pytest.mark.asyncio
async def test_http_annotations_require_revision_and_reject_stale_changes(coverage_session, monkeypatch):
    from smms.api import analyst

    @asynccontextmanager
    async def scope(_settings):
        yield coverage_session
    monkeypatch.setattr(analyst, "session_scope", scope)
    app = FastAPI()
    app.include_router(analyst.router)
    path = f"/api/radar/annotations/bsc/{TOKEN}"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get(path)).status_code == 404
        assert (await client.put(path, json={"note": "unsafe"})).status_code == 428
        created = await client.put(path, json={"expected_revision": 0, "note": "saved", "favorite": True})
        assert created.status_code == 200 and created.json()["revision"] == 1
        stale = await client.put(path, json={"expected_revision": 0, "note": "overwrite"})
        assert stale.status_code == 409
        assert (await client.get(path)).json()["note"] == "saved"
        updated = await client.put(path, json={"expected_revision": 1, "note": "next", "tags": [" beta ", "alpha", "alpha"]})
        assert updated.status_code == 200 and updated.json()["revision"] == 2
        assert updated.json()["tags"] == ["alpha", "beta"]
        assert (await client.delete(path, params={"expected_revision": 1})).status_code == 409
        assert (await client.delete(path)).status_code == 428
        assert (await client.delete(path, params={"expected_revision": 2})).status_code == 200
        assert (await client.get(path)).status_code == 404
        row = await coverage_session.scalar(select(RadarAnnotation))
        assert row.deleted and row.note is None and row.tags == []
        recreated = await client.put(path, json={"expected_revision": 0, "note": "recreated"})
        assert recreated.json()["revision"] == 4
        assert (await client.put(path, json={"expected_revision": 1, "note": "old editor"})).status_code == 409


@pytest.mark.asyncio
async def test_http_rule_create_update_and_read_expose_scope(coverage_session, monkeypatch):
    from smms.api import analyst

    @asynccontextmanager
    async def scope(_settings):
        yield coverage_session
    monkeypatch.setattr(analyst, "session_scope", scope)
    app = FastAPI()
    app.include_router(analyst.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/radar/alert-rules", json=payload(scope="TOKEN", token_address=TOKEN, request_id=str(uuid4())))
        assert response.status_code == 201
        rule = response.json()
        assert rule["scope"] == "TOKEN" and rule["token_address"] == TOKEN
        rules = (await client.get("/api/radar/alert-rules")).json()["rules"]
        assert rules[0]["scope"] == "TOKEN"
        wrong = await client.post(f"/api/radar/alert-rules/{rule['id']}/evaluate", json={"chain": "bsc", "token_address": OTHER, "state": {"smart_money_score": 99}})
        assert wrong.status_code == 200 and not wrong.json()["notified"]
        changed = await client.patch(f"/api/radar/alert-rules/{rule['id']}", json=payload(scope="GLOBAL"))
        assert changed.json()["scope"] == "GLOBAL" and changed.json()["token_address"] is None


@pytest.mark.asyncio
async def test_deleted_rule_request_cannot_resurrect_or_expand_scope(coverage_session, monkeypatch):
    from smms.api import analyst
    @asynccontextmanager
    async def scope(_settings):
        yield coverage_session
    monkeypatch.setattr(analyst, "session_scope", scope)
    app = FastAPI()
    app.include_router(analyst.router)
    data = payload(scope="TOKEN", token_address=TOKEN, request_id=str(uuid4()))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/radar/alert-rules", json=data)
        rule_id = created.json()["id"]
        path = f"/api/radar/alert-rules/{rule_id}"
        assert (await client.delete(path)).status_code == 200
        assert (await client.get(path)).status_code == 404
        assert (await client.get("/api/radar/alert-rules")).json()["rules"] == []
        assert (await client.post("/api/radar/alert-rules", json=data)).status_code == 409
        rule = await coverage_session.get(AlertRule, rule_id, populate_existing=True)
        assert rule.deleted and not rule.enabled and rule.token_address == TOKEN


@pytest.mark.parametrize("operation", ["create", "update", "rule"])
def test_concurrent_manual_writes_have_one_winner_or_one_rule(research_session, operation):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from sqlalchemy import text
    from sqlalchemy.orm import Session

    from smms.radar.annotations import AnnotationConflict, active_annotation, save_annotation

    bind = research_session.get_bind()
    if bind.dialect.name != "postgresql":
        pytest.skip("requires isolated PostgreSQL concurrent transactions")
    if operation == "update":
        save_annotation(research_session, chain="bsc", token=TOKEN, expected_revision=0, note="original")
    research_session.commit()
    barrier = Barrier(2)
    request_id = str(uuid4())
    def write(index):
        with Session(bind) as session:
            session.execute(text("SET LOCAL lock_timeout='5s'"))
            session.execute(text("SET LOCAL statement_timeout='10s'"))
            barrier.wait(timeout=5)
            try:
                if operation == "rule":
                    session.add(RadarAnnotation(chain="bsc", token_address=f"thread-{index}", note="keep unrelated"))
                    row = create_rule(session, payload(scope="TOKEN", token_address=TOKEN, request_id=request_id))
                    result = row.id
                else:
                    row = save_annotation(session, chain="bsc", token=TOKEN,
                        expected_revision=1 if operation == "update" else 0, note=f"writer-{index}")
                    result = row.revision
                session.commit()
                return result
            except AnnotationConflict:
                session.rollback()
                return "CONFLICT"
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(write, [0, 1]))
    if operation == "rule":
        assert results[0] == results[1]
        assert research_session.scalar(select(func.count()).select_from(AlertRule)) == 1
        assert research_session.scalar(select(func.count()).select_from(RadarAnnotation)) == 2
    else:
        assert results.count("CONFLICT") == 1
        row = active_annotation(research_session, "bsc", TOKEN)
        assert row.revision == (2 if operation == "update" else 1)
        assert row.note in {"writer-0", "writer-1"}


@pytest.mark.asyncio
async def test_rule_patch_preserves_omitted_scope_and_requires_explicit_global(coverage_session, monkeypatch):
    from smms.api import analyst
    @asynccontextmanager
    async def scope(_settings):
        yield coverage_session
    monkeypatch.setattr(analyst, "session_scope", scope)
    app = FastAPI()
    app.include_router(analyst.router)
    data = payload(scope="TOKEN", token_address=TOKEN, request_id=str(uuid4()))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = (await client.post("/api/radar/alert-rules", json=data)).json()
        path = f"/api/radar/alert-rules/{created['id']}"
        renamed = await client.patch(path, json=payload(name="rename without scope"))
        assert renamed.status_code == 200 and renamed.json()["scope"] == "TOKEN"
        assert renamed.json()["token_address"] == TOKEN
        wrong = await client.post(path + "/evaluate", json={"chain": "bsc", "token_address": OTHER, "state": {"smart_money_score": 99}})
        assert not wrong.json()["notified"]
        disabled = await client.patch(path, json={"enabled": False})
        assert disabled.status_code == 200 and not disabled.json()["enabled"] and disabled.json()["scope"] == "TOKEN"
        assert (await client.patch(path, json={"token_address": None})).status_code == 422
        assert (await client.patch(path, json={"request_id": None})).status_code == 422
        explicit = await client.patch(path, json={"scope": "GLOBAL", "enabled": True})
        assert explicit.status_code == 200 and explicit.json()["scope"] == "GLOBAL" and explicit.json()["token_address"] is None
        assert explicit.json()["request_id"] == created["request_id"]


def test_schema_default_keeps_raw_legacy_annotation_active(research_session):
    from sqlalchemy import text

    from smms.radar.annotations import active_annotation

    bind = research_session.get_bind()
    if bind.dialect.name == "postgresql":
        schema = bind.get_execution_options()["schema_translate_map"][None]
        assert schema.startswith("smms_research_")
        research_session.execute(text(f'SET LOCAL search_path TO "{schema}"'))
    research_session.execute(text("INSERT INTO radar_annotations(chain,token_address,favorite,tags,created_at,updated_at) "
        "VALUES('bsc',:token,false,'[]',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"), {"token": TOKEN})
    row = active_annotation(research_session, "bsc", TOKEN)
    assert row is not None and row.deleted is False and row.revision == 1

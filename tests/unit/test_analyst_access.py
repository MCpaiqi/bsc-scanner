"""Analyst authorization is a header-only boundary before persistent writes."""

import runpy
from contextlib import asynccontextmanager, nullcontext
from pathlib import Path

import pytest
from alembic import context
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.engine import make_url

from smms.api import analyst
from smms.config.settings import Settings, get_settings

KEY = "synthetic-analyst-credential-" + "a" * 32
TOKEN = "0x" + "a1" * 20
WRITES = [
    ("PUT", f"/api/radar/annotations/bsc/{TOKEN}", {"expected_revision": 0, "note": "fixture"}),
    ("DELETE", f"/api/radar/annotations/bsc/{TOKEN}?expected_revision=1", None),
    ("POST", "/api/radar/alert-rules", {"name": "fixture", "conditions": []}),
    ("PATCH", "/api/radar/alert-rules/1", {"enabled": False}),
    ("DELETE", "/api/radar/alert-rules/1", None),
    ("POST", "/api/radar/alert-rules/1/evaluate", {"chain": "bsc", "token_address": TOKEN, "state": {}}),
]


def client_for(monkeypatch, key=KEY):
    settings = Settings(_env_file=None, SMMS_ANALYST_KEY=key)
    app = FastAPI()
    app.dependency_overrides[get_settings] = lambda: settings
    app.include_router(analyst.router)

    @asynccontextmanager
    async def forbidden(_settings):
        pytest.fail("unauthorized request opened a database session")
        yield

    monkeypatch.setattr(analyst, "session_scope", forbidden)
    return TestClient(app)


@pytest.mark.parametrize("method,path,body", WRITES)
@pytest.mark.parametrize("key,headers,status", [
    (None, {}, 503), (KEY, {}, 401), (KEY, {"Authorization": "Bearer incorrect"}, 401),
])
def test_every_analyst_mutation_is_rejected_before_database(monkeypatch, method, path, body, key, headers, status):
    with client_for(monkeypatch, key) as client:
        response = client.request(method, path, json=body, headers=headers)
    assert response.status_code == status
    assert KEY not in response.text and "incorrect" not in response.text
    if status == 401:
        assert response.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.parametrize("headers", [
    {"Authorization": "Basic " + KEY}, {"Authorization": "Bearer"},
    {"Authorization": "Bearer " + KEY + " extra"}, {"Authorization": "Bearer " + "x" * 10000},
    [("Authorization", "Bearer " + KEY), ("Authorization", "Bearer " + KEY)],
    [(b"Authorization", b"Bearer " + b"\xff" * 32)],
])
def test_malformed_headers_never_grant_access(monkeypatch, headers):
    with client_for(monkeypatch) as client:
        response = client.request(*WRITES[0][:2], json=WRITES[0][2], headers=headers)
    assert response.status_code == 401 and KEY not in response.text


def test_status_is_public_header_only_and_not_cacheable(monkeypatch):
    with client_for(monkeypatch) as client:
        for kwargs in ({}, {"params": {"key": KEY}}, {"headers": {"Cookie": "analyst_key=" + KEY}}):
            response = client.get("/api/radar/access", **kwargs)
            assert response.status_code == 200
            assert response.json() == {"enabled": True, "authorized": False}
            assert response.headers["Cache-Control"] == "no-store"
        response = client.get("/api/radar/access", headers={"Authorization": "bearer " + KEY})
        assert response.json() == {"enabled": True, "authorized": True}
        assert KEY not in response.text


@pytest.mark.parametrize("key", [None, ""])
def test_unconfigured_status_is_read_only_even_with_a_header(monkeypatch, key):
    with client_for(monkeypatch, key) as client:
        response = client.get("/api/radar/access", headers={"Authorization": "Bearer " + KEY})
    assert response.json() == {"enabled": False, "authorized": False}


def test_reconfigured_server_rejects_old_key_and_accepts_current_key(monkeypatch):
    with client_for(monkeypatch) as client:
        assert client.get("/api/radar/access", headers={"Authorization": "Bearer " + KEY}).json()["authorized"]
        replacement = "synthetic-replacement-credential-" + "b" * 32
        client.app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, SMMS_ANALYST_KEY=replacement)
        assert not client.get("/api/radar/access", headers={"Authorization": "Bearer " + KEY}).json()["authorized"]
        assert client.put(f"/api/radar/annotations/bsc/{TOKEN}", json={"expected_revision": 0},
                          headers={"Authorization": "Bearer " + KEY}).status_code == 401
        # A valid key reaches endpoint validation, still without opening the DB.
        assert client.put("/api/radar/annotations/bsc/not-an-address", json={"expected_revision": 0},
                          headers={"Authorization": "Bearer " + replacement}).status_code == 400


def test_settings_redact_credentials_and_encode_structured_database_url():
    password = "synthetic-db-p@ss:/%$#"
    settings = Settings(_env_file=None, SMMS_ANALYST_KEY=KEY, DATABASE_HOST="db.internal",
                        DATABASE_PORT=5433, DATABASE_USER="postgres", DATABASE_NAME="smms",
                        DATABASE_PASSWORD=password)
    parsed = make_url(settings.database_url)
    assert (parsed.host, parsed.port, parsed.database, parsed.password) == ("db.internal", 5433, "smms", password)
    for value in (repr(settings), settings.model_dump_json()):
        assert KEY not in value and password not in value and "database_url" not in value


@pytest.mark.parametrize("key", ["short", "invalid!" * 8, "非ASCII" * 16])
def test_invalid_analyst_configuration_does_not_print_its_input(key):
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None, SMMS_ANALYST_KEY=key)
    assert key not in str(error.value)


def test_alembic_accepts_percent_encoded_password_without_connecting(monkeypatch):
    settings = Settings(_env_file=None, DATABASE_HOST="db.internal", DATABASE_PASSWORD="synthetic@%:/")
    import smms.config
    monkeypatch.setattr(smms.config, "get_settings", lambda: settings)
    monkeypatch.setattr(context, "config", Config(), raising=False)
    monkeypatch.setattr(context, "is_offline_mode", lambda: True)
    seen = {}
    monkeypatch.setattr(context, "configure", lambda **kwargs: seen.update(kwargs))
    monkeypatch.setattr(context, "begin_transaction", nullcontext)
    monkeypatch.setattr(context, "run_migrations", lambda: None)
    runpy.run_path(str(Path(__file__).parents[2] / "src/smms/db/migrations/env.py"))
    assert make_url(seen["url"]).password == "synthetic@%:/"

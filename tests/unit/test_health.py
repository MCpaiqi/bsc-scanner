from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from asyncpg import InvalidPasswordError
from fastapi.testclient import TestClient

from smms.api.app import _persisted_bsc_health, app
from smms.config.settings import get_settings


def test_health_endpoint_reports_paper_mode(monkeypatch) -> None:
    from smms.api import app as api

    async def unavailable(_settings=None):
        return "unavailable"

    monkeypatch.setattr(api, "check_database", unavailable)
    monkeypatch.setattr(api, "check_redis", unavailable)
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["trading_mode"] == "PAPER"
    assert payload["live_trading_enabled"] is False
    expected_bsc_rpc = "ok" if get_settings().bsc_rpc_endpoints else "not_configured"
    assert payload["bsc_rpc"] == expected_bsc_rpc
    assert payload["market_feed"] == "ready"


def test_health_projects_shared_worker_transport_state() -> None:
    now = datetime(2026, 9, 15, 12, tzinfo=UTC)
    result = _persisted_bsc_health(
        SimpleNamespace(
            status="RUNNING",
            transport="ws",
            provider="bsc_websocket",
            updated_at=now,
        ),
        SimpleNamespace(last_processed_block=123, updated_at=now),
        ws_configured=True,
        http_configured=True,
        now=now,
    )

    assert result["bsc_websocket"]["status"] == "HEALTHY"
    assert result["bsc_websocket"]["details"]["connected"] is True
    assert result["bsc_websocket"]["details"]["primary_transport"] == "websocket"
    assert result["bsc_websocket"]["details"]["fallback_transport"] == "http"
    assert result["bsc_http"]["status"] == "UNKNOWN"
    assert result["bsc_http"]["details"]["connected"] is False


def test_health_exposes_live_edge_fields_and_worker_running_is_not_live():
    now = datetime(2026, 9, 15, 12, tzinfo=UTC)
    result = _persisted_bsc_health(
        SimpleNamespace(status="RUNNING", transport="ws", provider="bsc_websocket",
                        updated_at=now, last_block=100, checkpoint_block=90, lag=10),
        SimpleNamespace(last_processed_block=90, updated_at=now),
        ws_configured=True, http_configured=True, now=now,
    )
    details = result["bsc_websocket"]["details"]
    assert details["head_block"] == 100
    assert details["checkpoint_block"] == 90
    assert details["block_lag"] == 10
    assert details["ingestion_phase"] == "CATCHING_UP"
    assert details["live_ready"] is False


@pytest.mark.asyncio
async def test_database_health_treats_driver_auth_failure_as_unavailable(monkeypatch):
    from smms.db import database

    class UnavailableEngine:
        @asynccontextmanager
        async def connect(self):
            raise InvalidPasswordError("synthetic authentication failure")
            yield

    monkeypatch.setattr(database, "get_engine", lambda _settings=None: UnavailableEngine())
    assert await database.check_database() == "unavailable"

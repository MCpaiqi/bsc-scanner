from datetime import UTC, datetime, timedelta

import httpx
import pytest

from smms.ingestion.market.service import MarketFeedService
from smms.observability import (
    CircuitBreaker,
    CircuitOpenError,
    MetricsRegistry,
    SourceHealthRegistry,
    SourceStatus,
    freshness_status,
)
from smms.radar.events import publish_redis


def test_source_registry_transitions_and_redacts_provider_details() -> None:
    now = datetime(2026, 9, 12, 12, tzinfo=UTC)
    registry = SourceHealthRegistry()
    registry.register("fixture", stale_after_seconds=10, down_after_seconds=30)
    registry.record_success(
        "fixture",
        observed_at=now,
        details={"endpoint": "https://private.example/key", "records": 2},
    )
    assert registry.get("fixture").status_at(now + timedelta(seconds=5)) is SourceStatus.HEALTHY
    assert registry.get("fixture").status_at(now + timedelta(seconds=15)) is SourceStatus.STALE
    registry.record_failure("fixture", "GET https://private.example/key failed")
    payload = registry.snapshot(now=now + timedelta(seconds=15))["fixture"]
    assert payload["status"] == "DEGRADED"
    assert "private.example" not in str(payload)
    assert payload["details"] == {"records": 2}


def test_metrics_cover_chain_ingestion_candidates_audit_providers_and_sse() -> None:
    registry = MetricsRegistry(window_seconds=60)
    registry.record_chain_head(100)
    registry.record_processed_block(97)
    registry.record_ingestion_event(decoded=True)
    registry.record_ingestion_event(decoded=False)
    registry.record_candidate_discovery()
    registry.record_audit(queue_depth=4, latency_ms=12, failed=True)
    registry.record_provider_request("goplus", latency_ms=8, success=False, rate_limited=True)
    registry.record_provider_request("goplus", latency_ms=4, success=True, cache_hit=True)
    registry.record_sse_client_count(3)
    registry.record_sse_event()
    snapshot = registry.snapshot()
    assert snapshot["chain"]["block_lag"] == 3
    assert snapshot["ingestion"]["decode_failures_total"] == 1
    assert snapshot["candidates"]["discovery_rate"] > 0
    assert snapshot["audit"]["queue_depth"] == 4
    assert snapshot["providers"]["goplus"]["rate_limited_count"] == 1
    assert snapshot["providers"]["goplus"]["cache_hit_rate"] == 1
    assert snapshot["sse"]["client_count"] == 3


def test_circuit_breaker_opens_then_recovers_with_half_open_probe() -> None:
    clock = [0.0]
    breaker = CircuitBreaker(
        failure_threshold=2,
        recovery_timeout_seconds=10,
        clock=lambda: clock[0],
    )
    breaker.record_failure("one")
    breaker.record_failure("two")
    assert breaker.state == "OPEN"
    assert breaker.allow_request() is False
    clock[0] = 10
    assert breaker.allow_request() is True
    assert breaker.state == "HALF_OPEN"
    with pytest.raises(CircuitOpenError):
        breaker.before_call()
    breaker.record_success()
    assert breaker.state == "CLOSED"


def test_freshness_never_treats_missing_timestamp_as_live() -> None:
    assert freshness_status(None) == "UNKNOWN"
    now = datetime(2026, 9, 12, 12, tzinfo=UTC)
    assert freshness_status(now - timedelta(seconds=2), now=now) == "FRESH"
    assert freshness_status(now - timedelta(seconds=90), now=now) == "STALE"
    assert freshness_status(now - timedelta(hours=2), now=now) == "EXPIRED"

def test_registry_redacts_authorization_and_nested_urls() -> None:
    registry = SourceHealthRegistry()
    registry.record_failure(
        "fixture",
        "Authorization: Bearer super-secret-token",
        details={"messages": ["https://private.example/token", "api_key=abc"]},
    )
    payload = registry.snapshot()["fixture"]
    rendered = str(payload)
    assert "super-secret-token" not in rendered
    assert "private.example" not in rendered
    assert "abc" not in rendered

@pytest.mark.asyncio
async def test_redis_fanout_failure_is_best_effort(monkeypatch) -> None:
    class BrokenRedis:
        async def publish(self, *_args, **_kwargs):
            raise RuntimeError("redis unavailable")
        async def aclose(self):
            return None
    monkeypatch.setattr("redis.asyncio.Redis.from_url", lambda *_args, **_kwargs: BrokenRedis())
    assert await publish_redis({"event": "x"}) is False


@pytest.mark.asyncio
async def test_market_provider_outage_returns_stale_snapshot_without_crashing() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(200, json={"tokens": [{"token_address": "0x1"}]})
        return httpx.Response(503)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://fixture.test"
    ) as client:
        class Provider:
            async def discover_latest_tokens(self, *, limit: int = 18):
                response = await client.get("/")
                response.raise_for_status()
                return response.json()

        now = datetime(2026, 9, 12, 12, tzinfo=UTC)
        service = MarketFeedService(
            provider=Provider(),
            ttl=timedelta(seconds=0),
            stale_ttl=timedelta(minutes=5),
            clock=lambda: now,
        )
        first = await service.latest_tokens(limit=1)
        stale = await service.latest_tokens(limit=1)
    assert first["freshness"] == "FRESH"
    assert stale["freshness"] == "STALE"
    assert stale["tokens"] == [{"token_address": "0x1"}]

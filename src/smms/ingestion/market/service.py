"""Shared cache and request coalescing for the public market feed."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from smms.observability import CircuitBreaker, CircuitOpenError, metrics, source_health_registry


class MarketProvider(Protocol):
    async def discover_latest_tokens(self, *, limit: int = 18) -> dict[str, Any]: ...


class MarketFeedService:
    def __init__(
        self,
        *,
        provider: MarketProvider,
        ttl: timedelta = timedelta(seconds=20),
        stale_ttl: timedelta = timedelta(minutes=5),
        clock: Callable[[], datetime] | None = None,
        provider_name: str = "dexscreener",
        circuit_breaker: CircuitBreaker | None = None,
    ) -> None:
        self.provider = provider
        self.ttl = ttl
        self.stale_ttl = stale_ttl
        self.clock = clock or (lambda: datetime.now(UTC))
        self.provider_name = provider_name
        self.circuit_breaker = circuit_breaker or CircuitBreaker()
        self._cache: dict[int, tuple[datetime, dict[str, Any]]] = {}
        self._lock = asyncio.Lock()

    async def latest_tokens(self, *, limit: int = 18) -> dict[str, Any]:
        cached = self._cache.get(limit)
        if cached and self.clock() - cached[0] < self.ttl:
            metrics.record_provider_request(self.provider_name, success=True, cache_hit=True)
            source_health_registry.record_cache(self.provider_name, True)
            return cached[1]

        async with self._lock:
            cached = self._cache.get(limit)
            if cached and self.clock() - cached[0] < self.ttl:
                metrics.record_provider_request(self.provider_name, success=True, cache_hit=True)
                source_health_registry.record_cache(self.provider_name, True)
                return cached[1]
            source_health_registry.record_cache(self.provider_name, False)
            try:
                self.circuit_breaker.before_call()
                started = self.clock()
                snapshot = await self.provider.discover_latest_tokens(limit=limit)
                elapsed_ms = max(0.0, (self.clock() - started).total_seconds() * 1000)
                self.circuit_breaker.record_success()
                metrics.record_provider_request(
                    self.provider_name, latency_ms=elapsed_ms, success=True
                )
                source_health_registry.record_success(
                    self.provider_name, latency_ms=elapsed_ms
                )
                observed = self.clock()
                if isinstance(snapshot, dict):
                    snapshot = {
                        **snapshot,
                        "observed_at": snapshot.get("observed_at") or observed.isoformat(),
                        "updated_at": snapshot.get("updated_at") or observed.isoformat(),
                        "freshness": "FRESH",
                        "stale": False,
                    }
                self._cache[limit] = (observed, snapshot)
                return snapshot
            except Exception as exc:  # noqa: BLE001 - stale evidence is safer than an outage
                self.circuit_breaker.record_failure(exc)
                metrics.record_provider_request(self.provider_name, success=False)
                source_health_registry.record_failure(self.provider_name, exc)
                if cached:
                    age = self.clock() - cached[0]
                    if age <= self.stale_ttl:
                        stale = dict(cached[1])
                        stale["freshness"] = "STALE"
                        stale["stale"] = True
                        stale["stale_reason"] = "provider_unavailable"
                        stale["data_status"] = "STALE"
                        return stale
                now = self.clock()
                return {
                    "chain": "bsc",
                    "source": self.provider_name,
                    "demo": False,
                    "tokens": [],
                    "observed_at": now.isoformat(),
                    "updated_at": None,
                    "freshness": "UNKNOWN",
                    "data_status": "UNKNOWN",
                    "provider_status": "DOWN" if isinstance(exc, CircuitOpenError) else "DEGRADED",
                }

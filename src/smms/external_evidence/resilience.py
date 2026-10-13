from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TypeVar

from smms.observability import CircuitBreaker, metrics, source_health_registry

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class CacheLookup[T]:
    value: T | None
    state: str  # MISS, FRESH, STALE
    fetched_at: datetime | None = None


@dataclass(slots=True)
class _Entry[T]:
    value: T
    fetched_at: datetime
    expires_at: datetime


class TTLCache[T]:
    def __init__(self, ttl: timedelta = timedelta(seconds=60), *, max_entries: int = 10_000, clock: Callable[[], datetime] | None = None) -> None:
        if ttl.total_seconds() <= 0 or max_entries < 1:
            raise ValueError("ttl must be positive and max_entries must be >= 1")
        self.ttl, self.max_entries = ttl, max_entries
        self.clock = clock or (lambda: datetime.now(UTC))
        self._items: dict[str, _Entry[T]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str, *, allow_stale: bool = False) -> CacheLookup[T]:
        async with self._lock:
            entry = self._items.get(key)
            if entry is None:
                return CacheLookup(None, "MISS")
            now = self.clock()
            if now < entry.expires_at:
                return CacheLookup(entry.value, "FRESH", entry.fetched_at)
            return CacheLookup(entry.value, "STALE", entry.fetched_at) if allow_stale else CacheLookup(None, "MISS", entry.fetched_at)

    async def set(self, key: str, value: T) -> None:
        async with self._lock:
            now = self.clock()
            self._items[key] = _Entry(value, now, now + self.ttl)
            if len(self._items) > self.max_entries:
                oldest = min(self._items, key=lambda item: self._items[item].fetched_at)
                self._items.pop(oldest, None)

    async def clear(self) -> None:
        async with self._lock:
            self._items.clear()


class RateLimiter:
    """Simple async spacing limiter (requests per second), safe across tasks."""

    def __init__(self, requests_per_second: float = 5.0) -> None:
        if requests_per_second <= 0:
            raise ValueError("requests_per_second must be positive")
        self.interval = 1.0 / requests_per_second
        self._next_at = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        loop = asyncio.get_running_loop()
        async with self._lock:
            now = loop.time()
            wait = max(0.0, self._next_at - now)
            self._next_at = max(now, self._next_at) + self.interval
        if wait:
            await asyncio.sleep(wait)


def backoff_delay(attempt: int, *, base: float = 0.5, maximum: float = 30.0, jitter: float = 0.1) -> float:
    if attempt < 0:
        raise ValueError("attempt must be non-negative")
    delay = min(maximum, base * 2**attempt)
    return max(0.0, delay + random.uniform(-jitter, jitter)) if jitter else delay


@dataclass(slots=True)
class ProviderHealth:
    consecutive_failures: int = 0
    total_requests: int = 0
    total_failures: int = 0
    last_error: str | None = None
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    rate_limited: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    circuit_state: str = "CLOSED"
    stale_fallbacks: int = 0

    @property
    def status(self) -> str:
        if self.consecutive_failures == 0:
            return "HEALTHY"
        return "UNHEALTHY" if self.consecutive_failures >= 3 else "DEGRADED"

    @property
    def stale(self) -> bool:
        return self.last_success_at is None or self.consecutive_failures > 0

    def record_success(self) -> None:
        self.total_requests += 1
        self.consecutive_failures = 0
        self.last_error = None
        self.last_success_at = datetime.now(UTC)
        self.circuit_state = "CLOSED"

    def record_failure(self, error: Exception | str) -> None:
        self.total_requests += 1
        self.total_failures += 1
        self.consecutive_failures += 1
        self.last_error = str(error)[:500]
        self.last_failure_at = datetime.now(UTC)

    @property
    def error_rate(self) -> float:
        return self.total_failures / self.total_requests if self.total_requests else 0.0

    @property
    def cache_hit_rate(self) -> float | None:
        total = self.cache_hits + self.cache_misses
        return self.cache_hits / total if total else None


class ProviderRuntime[T]:
    """Cache, request coalescing, rate limiting and retry policy for a provider."""

    def __init__(
        self,
        *,
        ttl: timedelta = timedelta(seconds=60),
        requests_per_second: float = 5.0,
        max_retries: int = 2,
        backoff_base: float = 0.5,
        cache: TTLCache[T] | None = None,
        limiter: RateLimiter | None = None,
        provider_name: str = "external_provider",
        circuit_breaker: CircuitBreaker | None = None,
        failure_threshold: int = 3,
        recovery_timeout_seconds: float = 30.0,
        stale_ttl: timedelta = timedelta(minutes=5),
    ) -> None:
        if stale_ttl.total_seconds() < 0:
            raise ValueError("stale_ttl must be non-negative")
        self.cache = cache or TTLCache(ttl)
        self.stale_ttl = stale_ttl
        self.limiter = limiter or RateLimiter(requests_per_second)
        self.max_retries, self.backoff_base = max_retries, backoff_base
        self.health = ProviderHealth()
        self.provider_name = provider_name
        self.circuit_breaker = circuit_breaker or CircuitBreaker(
            failure_threshold=failure_threshold,
            recovery_timeout_seconds=recovery_timeout_seconds,
        )
        self._inflight: dict[str, asyncio.Task[T]] = {}
        self._lock = asyncio.Lock()

    def _stale_cache_value(self, cached: CacheLookup[T]) -> T | None:
        if cached.value is None or cached.fetched_at is None:
            return None
        now = self.cache.clock()
        age = now - cached.fetched_at
        if age <= self.stale_ttl:
            self.health.stale_fallbacks += 1
            return cached.value
        return None

    async def fetch(self, key: str, operation: Callable[[], Awaitable[T]]) -> T | None:
        cached = await self.cache.get(key, allow_stale=True)
        if cached.state == "FRESH":
            self.health.cache_hits += 1
            metrics.record_provider_request(
                self.provider_name, success=True, cache_hit=True
            )
            source_health_registry.record_cache(self.provider_name, True)
            return cached.value
        self.health.cache_misses += 1
        source_health_registry.record_cache(self.provider_name, False)
        if not self.circuit_breaker.allow_request():
            self.health.circuit_state = self.circuit_breaker.state.value
            return self._stale_cache_value(cached)
        async with self._lock:
            task = self._inflight.get(key)
            if task is None:
                task = asyncio.create_task(self._run(key, operation))
                self._inflight[key] = task
        try:
            return await task
        except Exception:  # noqa: BLE001 - stale fallback must cover provider failures
            self.health.circuit_state = self.circuit_breaker.state.value
            return self._stale_cache_value(cached)
        finally:
            async with self._lock:
                if self._inflight.get(key) is task:
                    self._inflight.pop(key, None)

    async def _run(self, key: str, operation: Callable[[], Awaitable[T]]) -> T:
        last: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                await self.limiter.acquire()
                value = await operation()
                await self.cache.set(key, value)
                self.health.record_success()
                self.circuit_breaker.record_success()
                self.health.circuit_state = self.circuit_breaker.state.value
                metrics.record_provider_request(self.provider_name, success=True)
                source_health_registry.record_success(self.provider_name)
                return value
            except Exception as exc:  # noqa: BLE001 - provider failures are retried uniformly
                last = exc
                self.health.record_failure(exc)
                rate_limited = getattr(exc, "status_code", None) == 429
                if rate_limited:
                    self.health.rate_limited += 1
                self.circuit_breaker.record_failure(exc)
                self.health.circuit_state = self.circuit_breaker.state.value
                metrics.record_provider_request(
                    self.provider_name,
                    success=False,
                    rate_limited=rate_limited,
                )
                source_health_registry.record_failure(
                    self.provider_name, exc, rate_limited=rate_limited
                )
                if attempt < self.max_retries:
                    await asyncio.sleep(backoff_delay(attempt, base=self.backoff_base))
        raise last or RuntimeError("provider operation failed")

"""Production observability primitives for the Radar runtime.

The module deliberately keeps the operational interface small and in-memory.
Durable business state remains in PostgreSQL; these records describe what the
runtime knows *now* and are safe to expose through diagnostics endpoints.
"""

from __future__ import annotations

import inspect
import re
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from statistics import mean
from typing import Any, TypeVar


class SourceStatus(StrEnum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    STALE = "STALE"
    DOWN = "DOWN"
    UNKNOWN = "UNKNOWN"


class CircuitState(StrEnum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitOpenError(RuntimeError):
    """Raised when a provider call is rejected by an open circuit."""


ProviderCircuitOpen = CircuitOpenError


_SENSITIVE = re.compile(
    r"(?:api[_-]?key|authorization|auth|credential|password|secret|private|token|endpoint|url)",
    re.IGNORECASE,
)
_URL = re.compile(r"(?:https?|wss?)://[^\s]+", re.IGNORECASE)
_AUTH = re.compile(r"(?i)(authorization\s*[:=]\s*(?:bearer\s+)?|(?:api[_-]?key|access-token|token|password|secret)\s*[:=]\s*)[^,;\s]+")


def utc_now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime | None, *, fallback: datetime | None = None) -> datetime | None:
    if value is None:
        return fallback
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _safe_error(value: Exception | str | None) -> str | None:
    if value is None:
        return None
    text = _URL.sub("[redacted-url]", str(value))
    text = _AUTH.sub(lambda match: match.group(1) + "[redacted]", text)[:500]
    return text if text else None


def _safe_details(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if not value:
        return {}
    result: dict[str, Any] = {}
    for key, item in value.items():
        if _SENSITIVE.search(str(key)):
            continue
        if isinstance(item, str):
            result[str(key)] = _AUTH.sub(lambda match: match.group(1) + "[redacted]", _URL.sub("[redacted-url]", item))[:500]
        elif isinstance(item, (int, float, bool)) or item is None:
            result[str(key)] = item
        elif isinstance(item, (list, tuple)):
            result[str(key)] = [_AUTH.sub(lambda match: match.group(1) + "[redacted]", _URL.sub("[redacted-url]", str(part)))[:120] for part in item[:20]]
    return result


@dataclass(slots=True)
class SourceHealth:
    """Runtime health for one external or internal source."""

    source: str
    display_name: str | None = None
    enabled: bool = True
    stale_after_seconds: float = 60.0
    down_after_seconds: float = 300.0
    status_override: SourceStatus | None = None
    request_count: int = 0
    error_count: int = 0
    rate_limited_count: int = 0
    cache_hit_count: int = 0
    cache_miss_count: int = 0
    consecutive_failures: int = 0
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    last_observed_at: datetime | None = None
    last_error: str | None = None
    latency_ms: float | None = None
    _latencies: deque[float] = field(default_factory=lambda: deque(maxlen=200), repr=False)
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.source

    @property
    def error_rate(self) -> float:
        return self.error_count / self.request_count if self.request_count else 0.0

    @property
    def rate_limit_rate(self) -> float:
        return self.rate_limited_count / self.request_count if self.request_count else 0.0

    @property
    def cache_hit_rate(self) -> float | None:
        total = self.cache_hit_count + self.cache_miss_count
        return self.cache_hit_count / total if total else None

    def _computed_status(self, now: datetime) -> SourceStatus:
        if not self.enabled:
            return SourceStatus.UNKNOWN
        if self.status_override is not None:
            return self.status_override
        if self.last_success_at is None:
            if self.consecutive_failures >= 3:
                return SourceStatus.DOWN
            return SourceStatus.DEGRADED if self.consecutive_failures else SourceStatus.UNKNOWN
        age = max(0.0, (now - self.last_success_at).total_seconds())
        if self.consecutive_failures >= 3 or age >= self.down_after_seconds:
            return SourceStatus.DOWN
        if self.consecutive_failures:
            return SourceStatus.DEGRADED
        if age >= self.stale_after_seconds:
            return SourceStatus.STALE
        return SourceStatus.HEALTHY

    def status_at(self, now: datetime | None = None) -> SourceStatus:
        current = _aware(now, fallback=utc_now()) or utc_now()
        return self._computed_status(current)

    def record_success(
        self,
        *,
        observed_at: datetime | None = None,
        latency_ms: float | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        stamp = _aware(observed_at, fallback=utc_now()) or utc_now()
        self.request_count += 1
        self.consecutive_failures = 0
        self.last_success_at = stamp
        self.last_observed_at = stamp
        self.last_error = None
        self.status_override = None
        if latency_ms is not None:
            self.latency_ms = max(0.0, float(latency_ms))
            self._latencies.append(self.latency_ms)
        self.details.update(_safe_details(details))

    def record_failure(
        self,
        error: Exception | str | None = None,
        *,
        observed_at: datetime | None = None,
        latency_ms: float | None = None,
        rate_limited: bool = False,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        stamp = _aware(observed_at, fallback=utc_now()) or utc_now()
        self.request_count += 1
        self.error_count += 1
        self.consecutive_failures += 1
        self.last_failure_at = stamp
        self.last_observed_at = stamp
        self.last_error = _safe_error(error)
        if rate_limited:
            self.rate_limited_count += 1
        if latency_ms is not None:
            self.latency_ms = max(0.0, float(latency_ms))
            self._latencies.append(self.latency_ms)
        self.details.update(_safe_details(details))

    def record_cache(self, hit: bool) -> None:
        if hit:
            self.cache_hit_count += 1
        else:
            self.cache_miss_count += 1

    def set_status(self, status: SourceStatus | str, *, details: Mapping[str, Any] | None = None) -> None:
        self.status_override = SourceStatus(str(status).upper())
        self.details.update(_safe_details(details))

    def as_dict(self, *, now: datetime | None = None) -> dict[str, Any]:
        current = _aware(now, fallback=utc_now()) or utc_now()
        last_success = _aware(self.last_success_at)
        freshness_seconds = (
            max(0.0, (current - last_success).total_seconds()) if last_success else None
        )
        freshness = (
            freshness_status(
                last_success,
                now=current,
                fresh_after_seconds=self.stale_after_seconds,
                stale_after_seconds=self.down_after_seconds,
            )
            if last_success
            else "UNKNOWN"
        )
        return {
            "source": self.source,
            "name": self.display_name or self.source,
            "status": self.status_at(current).value,
            "enabled": self.enabled,
            "observed_at": self.last_observed_at,
            "last_success_at": last_success,
            "last_failure_at": _aware(self.last_failure_at),
            "freshness": freshness,
            "freshness_seconds": freshness_seconds,
            "request_count": self.request_count,
            "error_count": self.error_count,
            "error_rate": round(self.error_rate, 6),
            "rate_limited_count": self.rate_limited_count,
            "rate_limit_rate": round(self.rate_limit_rate, 6),
            "cache_hit_count": self.cache_hit_count,
            "cache_miss_count": self.cache_miss_count,
            "cache_hit_rate": (
                round(self.cache_hit_rate, 6) if self.cache_hit_rate is not None else None
            ),
            "consecutive_failures": self.consecutive_failures,
            "latency_ms": self.latency_ms,
            "last_error": self.last_error,
            "details": dict(self.details),
        }


SOURCE_ALIASES = {
    "bsc_ws": "bsc_websocket",
    "bsc_stream": "bsc_websocket",
    "websocket": "bsc_websocket",
    "bsc_rpc": "bsc_http",
    "bsc_http_polling": "bsc_http",
    "http": "bsc_http",
    "database": "postgresql",
    "postgres": "postgresql",
    "db": "postgresql",
    "market_provider": "dexscreener",
    "dex_screener": "dexscreener",
    "audit": "audit_queue",
    "event_stream": "sse",
}


class SourceHealthRegistry:
    """Registry for all sources shown by the operational diagnostics view."""

    DEFAULTS = (
        ("bsc_websocket", "BSC WebSocket", 30.0, 180.0),
        ("bsc_http", "BSC HTTP", 30.0, 180.0),
        ("postgresql", "PostgreSQL", 60.0, 300.0),
        ("redis", "Redis", 60.0, 300.0),
        ("dexscreener", "DexScreener", 60.0, 600.0),
        ("goplus", "GoPlus", 120.0, 900.0),
        ("audit_queue", "Audit Queue", 60.0, 900.0),
        ("sse", "SSE", 60.0, 300.0),
    )

    def __init__(self) -> None:
        self._records: dict[str, SourceHealth] = {}
        self.reset()

    @staticmethod
    def canonical_name(name: str) -> str:
        key = str(name).strip().lower().replace(" ", "_")
        return SOURCE_ALIASES.get(key, key)

    def reset(self) -> None:
        self._records.clear()
        for source, display, stale, down in self.DEFAULTS:
            self.register(
                source,
                display_name=display,
                stale_after_seconds=stale,
                down_after_seconds=down,
            )

    def register(
        self,
        name: str,
        *,
        display_name: str | None = None,
        enabled: bool = True,
        stale_after_seconds: float = 60.0,
        down_after_seconds: float = 300.0,
        details: Mapping[str, Any] | None = None,
    ) -> SourceHealth:
        key = self.canonical_name(name)
        if stale_after_seconds < 0 or down_after_seconds < stale_after_seconds:
            raise ValueError("down_after_seconds must be >= stale_after_seconds >= 0")
        record = self._records.get(key)
        if record is None:
            record = SourceHealth(
                source=key,
                display_name=display_name,
                enabled=enabled,
                stale_after_seconds=float(stale_after_seconds),
                down_after_seconds=float(down_after_seconds),
            )
            self._records[key] = record
        else:
            record.display_name = display_name or record.display_name
            record.enabled = enabled
            record.stale_after_seconds = float(stale_after_seconds)
            record.down_after_seconds = float(down_after_seconds)
            if not enabled:
                record.status_override = None
        record.details.update(_safe_details(details))
        return record

    def get(self, name: str) -> SourceHealth:
        key = self.canonical_name(name)
        return self._records.setdefault(key, SourceHealth(source=key))

    def record_success(self, name: str, **kwargs: Any) -> SourceHealth:
        record = self.get(name)
        record.record_success(**kwargs)
        return record

    def record_failure(self, name: str, error: Exception | str | None = None, **kwargs: Any) -> SourceHealth:
        record = self.get(name)
        record.record_failure(error, **kwargs)
        return record

    def record_cache(self, name: str, hit: bool) -> SourceHealth:
        record = self.get(name)
        record.record_cache(hit)
        return record

    def set_status(self, name: str, status: SourceStatus | str, **kwargs: Any) -> SourceHealth:
        record = self.get(name)
        record.set_status(status, **kwargs)
        return record

    def snapshot(self, *, now: datetime | None = None) -> dict[str, dict[str, Any]]:
        return {key: value.as_dict(now=now) for key, value in self._records.items()}

    health_snapshot = snapshot

    def statuses(self, *, now: datetime | None = None) -> dict[str, SourceStatus]:
        return {key: value.status_at(now) for key, value in self._records.items()}


@dataclass(slots=True)
class _ProviderMetric:
    requests: int = 0
    errors: int = 0
    rate_limited: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    latencies: deque[float] = field(default_factory=lambda: deque(maxlen=200))


class MetricsRegistry:
    """Bounded counters, gauges and rolling rates for Radar operations."""

    def __init__(self, *, window_seconds: float = 60.0, clock: Callable[[], float] | None = None) -> None:
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self.window_seconds = float(window_seconds)
        self.clock = clock or time.monotonic
        self._totals: defaultdict[str, float] = defaultdict(float)
        self._gauges: dict[str, float | int | None] = {}
        self._events: defaultdict[str, deque[float]] = defaultdict(lambda: deque(maxlen=10_000))
        self._observations: defaultdict[str, deque[float]] = defaultdict(lambda: deque(maxlen=200))
        self._providers: dict[str, _ProviderMetric] = {}

    def _timestamp(self, at: datetime | float | None = None) -> float:
        return float(at) if isinstance(at, (int, float)) else self.clock()

    def increment(self, name: str, value: float = 1.0, *, at: datetime | float | None = None) -> None:
        amount = float(value)
        self._totals[name] += amount
        stamp = self._timestamp(at)
        events = self._events[name]
        for _ in range(max(1, int(amount))):
            events.append(stamp)

    def set_gauge(self, name: str, value: float | None) -> None:
        self._gauges[name] = value

    def observe(self, name: str, value: float, *, at: datetime | float | None = None) -> None:
        self._observations[name].append(float(value))
        self.increment(f"{name}_observations", at=at)

    def _rate(self, name: str) -> float:
        now = self.clock()
        events = self._events[name]
        while events and events[0] < now - self.window_seconds:
            events.popleft()
        return len(events) / self.window_seconds

    def record_chain_head(self, block: int | None) -> None:
        self.set_gauge("chain_head", block)

    def record_processed_block(self, block: int | None) -> None:
        self.set_gauge("processed_block", block)
        head = self._gauges.get("chain_head")
        if block is not None and isinstance(head, (int, float)):
            self.set_gauge("block_lag", max(0, int(head) - int(block)))

    def record_ingestion_event(self, *, decoded: bool = True, latency_ms: float | None = None) -> None:
        self.increment("ingestion_events")
        if not decoded:
            self.increment("decode_failures")
        if latency_ms is not None:
            self.observe("ingestion_latency_ms", latency_ms)

    def record_candidate_discovery(self) -> None:
        self.increment("candidate_discoveries")

    def record_audit(
        self,
        *,
        queue_depth: int | None = None,
        latency_ms: float | None = None,
        failed: bool = False,
    ) -> None:
        if queue_depth is not None:
            self.set_gauge("audit_queue_depth", max(0, int(queue_depth)))
        if latency_ms is not None:
            self.observe("audit_latency_ms", latency_ms)
        self.increment("audit_jobs")
        if failed:
            self.increment("audit_failures")

    def record_provider_request(
        self,
        provider: str,
        *,
        latency_ms: float | None = None,
        success: bool = True,
        rate_limited: bool = False,
        cache_hit: bool | None = None,
    ) -> None:
        key = str(provider).lower()
        metric = self._providers.setdefault(key, _ProviderMetric())
        metric.requests += 1
        if not success:
            metric.errors += 1
        if rate_limited:
            metric.rate_limited += 1
        if cache_hit is True:
            metric.cache_hits += 1
        elif cache_hit is False:
            metric.cache_misses += 1
        if latency_ms is not None:
            metric.latencies.append(max(0.0, float(latency_ms)))

    def record_sse_client_count(self, count: int) -> None:
        self.set_gauge("sse_client_count", max(0, int(count)))

    def record_sse_event(self) -> None:
        self.increment("sse_events")

    @staticmethod
    def _percentile(values: deque[float], percentile: float) -> float | None:
        if not values:
            return None
        ordered = sorted(values)
        index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * percentile)))
        return ordered[index]

    def snapshot(self) -> dict[str, Any]:
        average_ingestion = self._observations["ingestion_latency_ms"]
        average_audit = self._observations["audit_latency_ms"]
        providers: dict[str, dict[str, Any]] = {}
        for name, metric in self._providers.items():
            providers[name] = {
                "request_count": metric.requests,
                "error_count": metric.errors,
                "error_rate": metric.errors / metric.requests if metric.requests else 0.0,
                "rate_limited_count": metric.rate_limited,
                "rate_limit_rate": metric.rate_limited / metric.requests if metric.requests else 0.0,
                "cache_hit_count": metric.cache_hits,
                "cache_miss_count": metric.cache_misses,
                "cache_hit_rate": (
                    metric.cache_hits / (metric.cache_hits + metric.cache_misses)
                    if metric.cache_hits + metric.cache_misses
                    else None
                ),
                "latency_ms": mean(metric.latencies) if metric.latencies else None,
                "latency_p95_ms": self._percentile(metric.latencies, 0.95),
            }
        ingestion_rate = self._rate("ingestion_events")
        decode_rate = self._rate("decode_failures")
        candidate_rate = self._rate("candidate_discoveries")
        audit_total = self._totals.get("audit_jobs", 0)
        audit_failures = self._totals.get("audit_failures", 0)
        sse_rate = self._rate("sse_events")
        result: dict[str, Any] = {
            "chain": {
                "head": self._gauges.get("chain_head"),
                "processed_block": self._gauges.get("processed_block"),
                "block_lag": self._gauges.get("block_lag"),
            },
            "ingestion": {
                "event_rate": ingestion_rate,
                "decode_failure_rate": decode_rate,
                "events_total": self._totals.get("ingestion_events", 0),
                "decode_failures_total": self._totals.get("decode_failures", 0),
                "latency_ms": mean(average_ingestion) if average_ingestion else None,
            },
            "candidates": {
                "discovery_rate": candidate_rate,
                "discoveries_total": self._totals.get("candidate_discoveries", 0),
            },
            "audit": {
                "queue_depth": self._gauges.get("audit_queue_depth", 0),
                "latency_ms": mean(average_audit) if average_audit else None,
                "failure_rate": audit_failures / audit_total if audit_total else 0.0,
                "jobs_total": audit_total,
                "failures_total": audit_failures,
            },
            "providers": providers,
            "sse": {
                "client_count": self._gauges.get("sse_client_count", 0),
                "event_rate": sse_rate,
                "events_total": self._totals.get("sse_events", 0),
            },
        }
        # Flat aliases keep dashboards and lightweight clients simple.
        result.update(
            {
                "chain_head": result["chain"]["head"],
                "processed_block": result["chain"]["processed_block"],
                "block_lag": result["chain"]["block_lag"],
                "event_ingestion_rate": ingestion_rate,
                "decode_failure_rate": decode_rate,
                "candidate_discovery_rate": candidate_rate,
                "audit_queue_depth": result["audit"]["queue_depth"],
                "audit_latency_ms": result["audit"]["latency_ms"],
                "audit_failure_rate": result["audit"]["failure_rate"],
                "provider_latency": {
                    name: data["latency_ms"] for name, data in providers.items()
                },
                "provider_error_rate": {
                    name: data["error_rate"] for name, data in providers.items()
                },
                "provider_429_rate": {
                    name: data["rate_limit_rate"] for name, data in providers.items()
                },
                "provider_cache_hit_rate": {
                    name: data["cache_hit_rate"] for name, data in providers.items()
                },
                "sse_client_count": result["sse"]["client_count"],
                "sse_event_rate": sse_rate,
            }
        )
        return result

    def reset(self) -> None:
        self._totals.clear()
        self._gauges.clear()
        self._events.clear()
        self._observations.clear()
        self._providers.clear()

    metrics_snapshot = snapshot


T = TypeVar("T")


class CircuitBreaker:
    """Small, deterministic circuit breaker suitable for async providers."""

    def __init__(
        self,
        *,
        failure_threshold: int = 3,
        recovery_timeout_seconds: float = 30.0,
        success_threshold: int = 1,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if failure_threshold < 1 or recovery_timeout_seconds < 0 or success_threshold < 1:
            raise ValueError("invalid circuit breaker thresholds")
        self.failure_threshold = int(failure_threshold)
        self.recovery_timeout_seconds = float(recovery_timeout_seconds)
        self.success_threshold = int(success_threshold)
        self.clock = clock or time.monotonic
        self._state = CircuitState.CLOSED
        self._failures = 0
        self._successes = 0
        self._opened_at: float | None = None
        self._probe_in_flight = False

    @property
    def state(self) -> CircuitState:
        self._ready_for_probe()
        return self._state

    @property
    def failure_count(self) -> int:
        return self._failures

    @property
    def opened_at(self) -> float | None:
        return self._opened_at

    @property
    def is_open(self) -> bool:
        return self.state == CircuitState.OPEN

    def _ready_for_probe(self) -> None:
        if (
            self._state == CircuitState.OPEN
            and self._opened_at is not None
            and self.clock() - self._opened_at >= self.recovery_timeout_seconds
        ):
            self._state = CircuitState.HALF_OPEN
            self._successes = 0
            self._probe_in_flight = False

    def allow_request(self) -> bool:
        self._ready_for_probe()
        if self._state == CircuitState.CLOSED:
            return True
        if self._state == CircuitState.OPEN:
            return False
        if self._probe_in_flight:
            return False
        self._probe_in_flight = True
        return True

    def before_call(self) -> None:
        if not self.allow_request():
            remaining = self.cooldown_remaining
            raise CircuitOpenError(
                f"provider circuit is open{f' for {remaining:.2f}s' if remaining else ''}"
            )

    @property
    def cooldown_remaining(self) -> float:
        if self._state != CircuitState.OPEN or self._opened_at is None:
            return 0.0
        return max(0.0, self.recovery_timeout_seconds - (self.clock() - self._opened_at))

    def record_success(self) -> None:
        if self._state == CircuitState.HALF_OPEN:
            self._successes += 1
            self._probe_in_flight = False
            if self._successes >= self.success_threshold:
                self.reset()
            return
        self._failures = 0
        self._probe_in_flight = False

    def record_failure(self, error: Exception | str | None = None) -> None:
        del error  # The source registry retains bounded error text.
        if self._state == CircuitState.HALF_OPEN:
            self._state = CircuitState.OPEN
            self._opened_at = self.clock()
            self._probe_in_flight = False
            return
        self._failures += 1
        self._probe_in_flight = False
        if self._failures >= self.failure_threshold:
            self._state = CircuitState.OPEN
            self._opened_at = self.clock()

    def reset(self) -> None:
        self._state = CircuitState.CLOSED
        self._failures = 0
        self._successes = 0
        self._opened_at = None
        self._probe_in_flight = False

    async def call(self, operation: Callable[[], Awaitable[T] | T]) -> T:
        self.before_call()
        try:
            result = operation()
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:
            self.record_failure(exc)
            raise
        self.record_success()
        return result  # type: ignore[return-value]


def freshness_status(
    observed_at: datetime | None,
    *,
    updated_at: datetime | None = None,
    now: datetime | None = None,
    fresh_after_seconds: float = 60.0,
    stale_after_seconds: float = 3600.0,
) -> str:
    """Classify a data block without ever treating an unknown timestamp as live."""

    stamp = _aware(updated_at or observed_at)
    if stamp is None:
        return "UNKNOWN"
    if fresh_after_seconds < 0 or stale_after_seconds < fresh_after_seconds:
        raise ValueError("stale_after_seconds must be >= fresh_after_seconds >= 0")
    current = _aware(now, fallback=utc_now()) or utc_now()
    age = max(0.0, (current - stamp).total_seconds())
    if age <= fresh_after_seconds:
        return "FRESH"
    if age <= stale_after_seconds:
        return "STALE"
    return "EXPIRED"


class Freshness(StrEnum):
    FRESH = "FRESH"
    STALE = "STALE"
    EXPIRED = "EXPIRED"
    UNKNOWN = "UNKNOWN"


class Observability:
    def __init__(self) -> None:
        self.sources = SourceHealthRegistry()
        self.metrics = MetricsRegistry()

    def reset(self) -> None:
        self.sources.reset()
        self.metrics.reset()

    @staticmethod
    def overall_status(sources: Mapping[str, Mapping[str, Any]]) -> SourceStatus:
        statuses = {str(item.get("status", "UNKNOWN")).upper() for item in sources.values()}
        if "DOWN" in statuses:
            return SourceStatus.DOWN
        if "DEGRADED" in statuses:
            return SourceStatus.DEGRADED
        if "STALE" in statuses:
            return SourceStatus.STALE
        if statuses and statuses <= {"UNKNOWN"}:
            return SourceStatus.UNKNOWN
        return SourceStatus.HEALTHY

    def snapshot(self, *, now: datetime | None = None) -> dict[str, Any]:
        sources = self.sources.snapshot(now=now)
        return {
            "status": self.overall_status(sources).value,
            "observed_at": _aware(now, fallback=utc_now()),
            "sources": sources,
            "metrics": self.metrics.snapshot(),
        }


observability = Observability()
source_health_registry = observability.sources
metrics_registry = observability.metrics
# Short names are convenient for adapters and retain a single process registry.
registry = source_health_registry
metrics = metrics_registry


__all__ = [
    "CircuitBreaker",
    "CircuitOpenError",
    "CircuitState",
    "Freshness",
    "HealthStatus",
    "MetricsRegistry",
    "Observability",
    "ProviderCircuitBreaker",
    "ProviderCircuitOpen",
    "SourceHealth",
    "SourceHealthRegistry",
    "SourceHealthStatus",
    "SourceStatus",
    "classify_freshness",
    "freshness_status",
    "metrics",
    "metrics_registry",
    "observability",
    "registry",
    "source_health_registry",
    "utc_now",
]


classify_freshness = freshness_status
HealthStatus = SourceStatus
SourceHealthStatus = SourceStatus
ProviderCircuitBreaker = CircuitBreaker

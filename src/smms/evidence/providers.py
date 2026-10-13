"""Read-only secondary evidence provider architecture.

Provider payloads are deliberately reduced to bounded, field-level observations.
No provider in this module writes risk snapshots or stores an unbounded response.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx

from smms.chains.constants import BSC_CHAIN_ID, normalize_address
from smms.external_evidence.models import EvidenceObservation
from smms.observability import CircuitBreaker, CircuitOpenError, metrics, source_health_registry


class ExternalEvidenceProvider(Protocol):
    """Capabilities exposed by a secondary evidence source."""

    async def fetch_token_market(self, token_address: str) -> list[EvidenceObservation]: ...

    async def fetch_token_security(self, token_address: str) -> list[EvidenceObservation]: ...

    async def fetch_holder_information(self, token_address: str) -> list[EvidenceObservation]: ...

    async def fetch_social_metadata(self, token_address: str) -> list[EvidenceObservation]: ...


@dataclass(slots=True)
class ProviderHealth:
    provider: str
    enabled: bool = True
    status: str = "UNKNOWN"
    last_success_at: datetime | None = None
    last_error_at: datetime | None = None
    last_error: str | None = None
    consecutive_failures: int = 0
    requests: int = 0
    failures: int = 0
    rate_limited: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    latency_ms: float | None = None
    circuit_state: str = "CLOSED"

    @property
    def error_rate(self) -> float:
        return self.failures / self.requests if self.requests else 0.0

    @property
    def cache_hit_rate(self) -> float | None:
        total = self.cache_hits + self.cache_misses
        return self.cache_hits / total if total else None


@dataclass(slots=True)
class _CacheEntry:
    observations: list[EvidenceObservation]
    fetched_at: datetime
    chain: str | None = None


class _HTTPProvider:
    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        client: httpx.AsyncClient | None = None,
        timeout: float = 10,
        ttl: float = 30,
        stale_ttl: float = 300,
        rate_limit: float = 0,
        max_retries: int = 2,
        backoff: float = 0.25,
        enabled: bool = True,
        failure_threshold: int = 3,
        recovery_timeout_seconds: float = 30.0,
        circuit_breaker: CircuitBreaker | None = None,
    ) -> None:
        self.name, self.base_url = name, base_url.rstrip("/")
        self.client, self.timeout = client, timeout
        self.ttl, self.stale_ttl = ttl, stale_ttl
        self.rate_limit, self.max_retries, self.backoff = rate_limit, max_retries, backoff
        self.enabled = enabled
        self.health = ProviderHealth(name, enabled=enabled, status="DISABLED" if not enabled else "UNKNOWN")
        self.circuit_breaker = circuit_breaker or CircuitBreaker(
            failure_threshold=failure_threshold,
            recovery_timeout_seconds=recovery_timeout_seconds,
        )
        self._cache: dict[tuple[str, str], _CacheEntry] = {}
        self._lock = asyncio.Lock()
        self._inflight: dict[tuple[str, str], asyncio.Task[list[EvidenceObservation]]] = {}
        self._last_request = 0.0

    async def _request_json(self, path: str, params: Mapping[str, str] | None = None, *, headers: Mapping[str, str] | None = None) -> Any:
        if not self.enabled:
            raise RuntimeError(f"{self.name} provider is disabled")
        if not self.circuit_breaker.allow_request():
            self.health.circuit_state = self.circuit_breaker.state.value
            raise CircuitOpenError(f"{self.name} provider circuit is open")
        owns_client = self.client is None
        client = self.client or httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout)
        try:
            for attempt in range(self.max_retries + 1):
                async with self._lock:
                    wait = self.rate_limit - (time.monotonic() - self._last_request)
                    if wait > 0:
                        await asyncio.sleep(wait)
                    self._last_request = time.monotonic()
                self.health.requests += 1
                started = time.perf_counter()
                rate_limit_recorded = False
                try:
                    response = await client.get(path, params=params, headers=headers)
                    if response.status_code == 429:
                        rate_limit_recorded = True
                        self.health.rate_limited += 1
                        latency_ms = (time.perf_counter() - started) * 1000
                        self.health.failures += 1
                        self.health.consecutive_failures += 1
                        self.health.last_error_at = datetime.now(UTC)
                        self.health.last_error = "HTTPStatusError"
                        self.health.status = "DEGRADED"
                        self.health.latency_ms = latency_ms
                        self.circuit_breaker.record_failure("HTTP 429")
                        self.health.circuit_state = self.circuit_breaker.state.value
                        metrics.record_provider_request(
                            self.name,
                            latency_ms=latency_ms,
                            success=False,
                            rate_limited=True,
                        )
                        source_health_registry.record_failure(
                            self.name,
                            "provider returned HTTP 429",
                            latency_ms=latency_ms,
                            rate_limited=True,
                        )
                        if attempt < self.max_retries:
                            if self.circuit_breaker.is_open:
                                raise CircuitOpenError(f"{self.name} provider circuit is open")
                            await asyncio.sleep(self.backoff * (2**attempt))
                            continue
                    response.raise_for_status()
                    payload = response.json()
                    if not isinstance(payload, (dict, list)):
                        raise TypeError("provider returned malformed JSON payload")
                    latency_ms = (time.perf_counter() - started) * 1000
                    self.health.status = "HEALTHY"
                    self.health.last_success_at = datetime.now(UTC)
                    self.health.last_error = None
                    self.health.consecutive_failures = 0
                    self.health.latency_ms = latency_ms
                    self.circuit_breaker.record_success()
                    self.health.circuit_state = self.circuit_breaker.state.value
                    metrics.record_provider_request(
                        self.name, latency_ms=latency_ms, success=True
                    )
                    source_health_registry.record_success(self.name, latency_ms=latency_ms)
                    return payload
                except (httpx.TimeoutException, httpx.HTTPError, ValueError, TypeError) as exc:
                    latency_ms = (time.perf_counter() - started) * 1000
                    rate_limited = (
                        isinstance(exc, httpx.HTTPStatusError)
                        and exc.response is not None
                        and exc.response.status_code == 429
                    )
                    if not rate_limit_recorded:
                        self.health.last_error_at = datetime.now(UTC)
                        self.health.last_error = type(exc).__name__
                        self.health.consecutive_failures += 1
                        self.health.failures += 1
                        self.health.status = "DEGRADED"
                        if rate_limited:
                            self.health.rate_limited += 1
                        self.health.latency_ms = latency_ms
                        self.circuit_breaker.record_failure(exc)
                        self.health.circuit_state = self.circuit_breaker.state.value
                        metrics.record_provider_request(
                            self.name,
                            latency_ms=latency_ms,
                            success=False,
                            rate_limited=rate_limited,
                        )
                        source_health_registry.record_failure(
                            self.name,
                            exc,
                            latency_ms=latency_ms,
                            rate_limited=rate_limited,
                        )
                    if attempt >= self.max_retries:
                        raise
                    if self.circuit_breaker.is_open:
                        raise CircuitOpenError(f"{self.name} provider circuit is open") from exc
                    await asyncio.sleep(self.backoff * (2**attempt))
            raise RuntimeError("provider request exhausted retries")
        finally:
            if owns_client:
                await client.aclose()

    async def _cached(
        self, capability: str, address: str, fetch: Any
    ) -> list[EvidenceObservation]:
        # Keep the historical key shape for callers that inspect the cache,
        # while storing the chain on the entry so a reused provider cannot
        # accidentally serve evidence from another network.
        key = (capability, normalize_address(address, chain="bsc"))
        chain = getattr(self, "chain", None)
        now = datetime.now(UTC)
        cached = self._cache.get(key)
        if cached and cached.chain != chain:
            cached = None
        if cached and (now - cached.fetched_at).total_seconds() <= self.ttl:
            self.health.cache_hits += 1
            metrics.record_provider_request(self.name, success=True, cache_hit=True)
            source_health_registry.record_cache(self.name, True)
            return cached.observations
        self.health.cache_misses += 1
        metrics.record_provider_request(self.name, success=True, cache_hit=False)
        source_health_registry.record_cache(self.name, False)
        try:
            async with self._lock:
                task = self._inflight.get(key)
                if task is None:
                    task = asyncio.create_task(fetch())
                    self._inflight[key] = task
            try:
                observations = await task
            finally:
                async with self._lock:
                    if self._inflight.get(key) is task:
                        self._inflight.pop(key, None)
            self._cache[key] = _CacheEntry(observations, now, chain)
            return observations
        except (httpx.HTTPError, TypeError, ValueError, KeyError, RuntimeError):
            if cached and (now - cached.fetched_at).total_seconds() <= self.stale_ttl:
                return [EvidenceObservation(
                    chain=o.chain, subject_type=o.subject_type, subject_address=o.subject_address,
                    field=o.field, value=o.value, source=o.source, source_type=o.source_type,
                    observed_at=o.observed_at, fetched_at=o.fetched_at, confidence=o.confidence,
                    evidence_level=o.evidence_level, raw_reference=o.raw_reference,
                    expires_at=o.expires_at, stale=True,
                ) for o in cached.observations]
            return [self._unknown(address, capability, stale=False)]

    def _unknown(self, address: str, capability: str, *, stale: bool) -> EvidenceObservation:
        return EvidenceObservation(
            chain=getattr(self, "chain", "unknown"), subject_type="token", subject_address=address,
            field=capability, value=None, source=self.name, confidence=0.0,
            evidence_level="UNKNOWN", raw_reference=f"{self.name}:{capability}:{address}",
            observed_at=datetime.now(UTC), fetched_at=datetime.now(UTC), stale=stale,
        )

    @staticmethod
    def _address(chain_or_address: str, token_address: str | None) -> str:
        """Accept both the concise token-only API and the protocol's chain/token form."""
        return token_address or chain_or_address

    @staticmethod
    def _observations(*, chain: str, address: str, source: str, values: Mapping[str, Any],
                      reference: str, evidence_level: str = "TRUSTED_PROVIDER") -> list[EvidenceObservation]:
        now = datetime.now(UTC)
        return [EvidenceObservation(chain, "token", address, key, value, source,
                                    observed_at=now, fetched_at=now,
                                    confidence=0.8 if value is not None else 0.0,
                                    evidence_level=evidence_level if value is not None else "UNKNOWN",
                                    raw_reference=reference)
                for key, value in values.items()]


class DexScreenerEvidenceProvider(_HTTPProvider):
    """DexScreener market, pair metadata and social enrichment."""

    def __init__(self, *, chain: str = "bsc", base_url: str = "https://api.dexscreener.com", **kwargs: Any) -> None:
        super().__init__(name="dexscreener", base_url=base_url, **kwargs)
        self.chain = chain

    async def _fetch_pairs(self, address: str) -> list[dict[str, Any]]:
        payload = await self._request_json(f"/tokens/v1/{self.chain}/{address}")
        if not isinstance(payload, list):
            raise TypeError("DexScreener pair payload must be a list")
        pairs = [p for p in payload if isinstance(p, dict)]
        pairs.sort(key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0), reverse=True)
        return pairs

    async def fetch_token_market(self, chain_or_address: str, token_address: str | None = None) -> list[EvidenceObservation]:
        token_address = self._address(chain_or_address, token_address)
        async def fetch() -> list[EvidenceObservation]:
            pairs = await self._fetch_pairs(token_address)
            if not pairs:
                return [self._unknown(token_address, "market", stale=False)]
            p = pairs[0]; volume = p.get("volume") or {}; liquidity = p.get("liquidity") or {}
            values = {"price_usd": p.get("priceUsd"), "liquidity_usd": liquidity.get("usd"),
                      "market_cap_usd": p.get("marketCap", p.get("fdv")), "volume_h24": volume.get("h24"),
                      "pair_address": p.get("pairAddress"), "dex_id": p.get("dexId")}
            return self._observations(chain=self.chain, address=token_address, source=self.name,
                                      values=values, reference=f"dexscreener:pair:{self.chain}:{token_address}")
        return await self._cached("market", token_address, fetch)

    async def fetch_social_metadata(self, chain_or_address: str, token_address: str | None = None) -> list[EvidenceObservation]:
        token_address = self._address(chain_or_address, token_address)
        async def fetch() -> list[EvidenceObservation]:
            pairs = await self._fetch_pairs(token_address)
            p = pairs[0] if pairs else {}
            info = p.get("info") or {}
            values = {"website": info.get("websites", [{}])[0].get("url") if info.get("websites") else None,
                      "social_links": info.get("socials")}
            return self._observations(chain=self.chain, address=token_address, source=self.name,
                                      values=values, reference=f"dexscreener:social:{self.chain}:{token_address}")
        return await self._cached("social", token_address, fetch)

    async def fetch_token_security(self, chain_or_address: str, token_address: str | None = None) -> list[EvidenceObservation]:
        return []

    async def fetch_holder_information(self, chain_or_address: str, token_address: str | None = None) -> list[EvidenceObservation]:
        return []


class GoPlusEvidenceProvider(_HTTPProvider):
    """GoPlus security enrichment. Values are trusted secondary evidence only."""

    def __init__(self, *, chain_id: str = str(BSC_CHAIN_ID), base_url: str = "https://api.gopluslabs.io", api_key: str | None = None, app_key: str | None = None, app_secret: str | None = None, access_token_manager: Any = None, **kwargs: Any) -> None:
        super().__init__(name="goplus", base_url=base_url, **kwargs)
        self.chain_id, self.api_key, self.chain = chain_id, api_key, chain_id
        self.app_key, self.app_secret = app_key, app_secret
        self.access_token_manager = access_token_manager

    def _auth_ready(self) -> bool:
        # BSC production access uses GoPlus app-key/app-secret token auth.
        # Missing credentials are an explicit evidence gap, never a SAFE result.
        return self.chain_id != str(BSC_CHAIN_ID) or bool(
            self.app_key and self.app_secret and self.access_token_manager
        )

    async def _security(self, address: str) -> Mapping[str, Any]:
        params = {"contract_addresses": address}
        headers: dict[str, str] = {}
        if self.access_token_manager is not None:
            headers["Authorization"] = f"Bearer {await self.access_token_manager.get_token()}"
        payload = await self._request_json(f"/api/v1/token_security/{self.chain_id}", params=params, headers=headers or None)
        result = payload.get("result") if isinstance(payload, dict) else None
        if not isinstance(result, dict):
            raise TypeError("GoPlus security payload missing result")
        item = result.get(address) or result.get(normalize_address(address, chain="bsc"))
        if not isinstance(item, dict):
            raise TypeError("GoPlus security payload missing token")
        return item

    async def fetch_token_security(self, chain_or_address: str, token_address: str | None = None) -> list[EvidenceObservation]:
        token_address = self._address(chain_or_address, token_address)
        if not self._auth_ready():
            self.health.status = "DEGRADED"
            self.health.last_error = "APP_KEY_SECRET_REQUIRED"
            return [self._unknown(token_address, "security", stale=False)]
        async def fetch() -> list[EvidenceObservation]:
            item = await self._security(token_address)
            values = {k: item.get(k) for k in ("is_honeypot", "buy_tax", "sell_tax", "is_blacklisted",
                      "is_mintable", "can_take_back_ownership", "owner_change_balance", "is_proxy",
                      "trading_cooldown", "cannot_buy", "cannot_sell")}
            return self._observations(chain=self.chain, address=token_address, source=self.name,
                                      values=values, reference=f"goplus:security:{self.chain_id}:{token_address}")
        return await self._cached("security", token_address, fetch)

    async def fetch_holder_information(self, chain_or_address: str, token_address: str | None = None) -> list[EvidenceObservation]:
        token_address = self._address(chain_or_address, token_address)
        if not self._auth_ready():
            self.health.status = "DEGRADED"
            self.health.last_error = "APP_KEY_SECRET_REQUIRED"
            return [self._unknown(token_address, "holders", stale=False)]
        async def fetch() -> list[EvidenceObservation]:
            item = await self._security(token_address)
            values = {k: item.get(k) for k in ("holder_count", "holders", "lp_holder_count", "lp_total_supply")}
            return self._observations(chain=self.chain, address=token_address, source=self.name,
                                      values=values, reference=f"goplus:holders:{self.chain_id}:{token_address}")
        return await self._cached("holders", token_address, fetch)

    async def fetch_token_market(self, chain_or_address: str, token_address: str | None = None) -> list[EvidenceObservation]:
        return []

    async def fetch_social_metadata(self, chain_or_address: str, token_address: str | None = None) -> list[EvidenceObservation]:
        return []


# Short aliases keep provider selection ergonomic while retaining explicit names.
DexScreenerProvider = DexScreenerEvidenceProvider
GoPlusProvider = GoPlusEvidenceProvider

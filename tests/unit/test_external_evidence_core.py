import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from smms.external_evidence import (
    Capability,
    EvidenceLevel,
    EvidenceObservation,
    ExternalEvidenceProviderBase,
    ProviderRuntime,
    TTLCache,
    UnsupportedCapability,
    sanitize_raw_reference,
)


def test_observation_normalizes_provenance_and_redacts_raw_reference() -> None:
    now = datetime.now(UTC)
    observation = EvidenceObservation(
        chain="solana",
        subject_type="TOKEN",
        subject_address="mint",
        field="price_usd",
        value="1.2",
        source="dexscreener",
        source_type="secondary_api",
        observed_at=now,
        fetched_at=now,
        confidence=0.8,
        evidence_level=EvidenceLevel.TRUSTED_PROVIDER,
        raw_reference={"request": {"api_key": "secret", "path": "/tokens/mint"}},
    )
    assert observation.evidence_level == "TRUSTED_PROVIDER"
    assert "api_key" not in str(observation.raw_reference)
    assert observation.as_dict()["observed_at"].endswith("+00:00")


def test_raw_reference_is_bounded() -> None:
    result = sanitize_raw_reference({"payload": "x" * 10_000}, max_bytes=100)
    assert len(str(result)) < 200
    assert result["truncated"] is True


def test_partial_provider_capability_is_explicit() -> None:
    class MarketOnly(ExternalEvidenceProviderBase):
        provider_name = "fixture"
        capabilities = frozenset({Capability.TOKEN_MARKET})

    with pytest.raises(UnsupportedCapability):
        asyncio.run(MarketOnly().fetch_token_security("solana", "mint"))


@pytest.mark.asyncio
async def test_runtime_cache_coalesces_and_returns_stale_on_failure() -> None:
    runtime = ProviderRuntime(ttl=timedelta(milliseconds=1), max_retries=0)
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        return "value"

    assert await runtime.fetch("token:market", operation) == "value"
    assert await runtime.fetch("token:market", operation) == "value"
    assert calls == 1

    await runtime.cache.set("token:stale", "old")

    async def failing() -> str:
        raise TimeoutError("fixture timeout")

    # The stale value is served when refresh fails; no network is involved.
    await runtime.cache.set("token:stale", "old")
    await asyncio.sleep(0.005)
    assert await runtime.fetch("token:stale", failing) == "old"
    assert runtime.health.total_failures == 1


@pytest.mark.asyncio
async def test_ttl_cache_marks_stale() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    current = [now]
    cache = TTLCache(timedelta(seconds=1), clock=lambda: current[0])
    await cache.set("k", 1)
    current[0] += timedelta(seconds=2)
    assert (await cache.get("k")).state == "MISS"
    assert (await cache.get("k", allow_stale=True)).state == "STALE"

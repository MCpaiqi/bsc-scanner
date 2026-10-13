from datetime import UTC, datetime, timedelta

import httpx
import pytest

from smms.external_evidence import (
    DexScreenerEvidenceProvider,
    EvidenceLevel,
    GoPlusEvidenceProvider,
)


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://fixture.test")


@pytest.mark.asyncio
async def test_dexscreener_market_maps_partial_payload_and_caches() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=[{
            "priceUsd": "1.25",
            "liquidity": {"usd": 9000},
            "volume": {"h24": 1200},
            "pairAddress": "pair",
            "dexId": "raydium",
        }])

    async with _client(handler) as client:
        provider = DexScreenerEvidenceProvider(client=client, chain="solana", ttl=60)
        observations = await provider.fetch_token_market("mint")
        again = await provider.fetch_token_market("solana", "mint")
    assert calls == 1
    assert {item.field for item in observations} >= {"price_usd", "liquidity_usd", "volume_h24"}
    assert next(item for item in observations if item.field == "price_usd").value == "1.25"
    assert again[0].evidence_level == EvidenceLevel.TRUSTED_PROVIDER.value


@pytest.mark.asyncio
async def test_goplus_security_is_trusted_provider_not_on_chain_verified() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": {"MINT": {
            "is_honeypot": "1", "buy_tax": "10", "is_proxy": "0",
        }}})

    async with _client(handler) as client:
        provider = GoPlusEvidenceProvider(client=client, chain_id="solana", ttl=60)
        observations = await provider.fetch_token_security("MINT")
    assert any(item.field == "is_honeypot" and item.value == "1" for item in observations)
    assert all(item.evidence_level == EvidenceLevel.TRUSTED_PROVIDER.value for item in observations if item.value is not None)
    assert all(item.evidence_level != "ON_CHAIN_VERIFIED" for item in observations)


@pytest.mark.asyncio
async def test_timeout_429_and_malformed_payload_return_unknown_without_network_retry_loop() -> None:
    responses = [
        httpx.Response(429),
        httpx.Response(200, json={"unexpected": True}),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return responses.pop(0)

    async with _client(handler) as client:
        provider = DexScreenerEvidenceProvider(client=client, max_retries=0, backoff=0, ttl=0)
        observations = await provider.fetch_token_market("mint")
    assert len(observations) == 1
    assert observations[0].evidence_level == EvidenceLevel.UNKNOWN.value


@pytest.mark.asyncio
async def test_stale_cache_is_marked_when_refresh_fails() -> None:
    now = datetime.now(UTC)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(200, json=[{"priceUsd": "2"}])
        raise httpx.ReadTimeout("fixture timeout", request=request)

    async with _client(handler) as client:
        provider = DexScreenerEvidenceProvider(client=client, max_retries=0, ttl=0, stale_ttl=60, backoff=0)
        first = await provider.fetch_token_market("mint")
        provider._cache[("market", "mint")].fetched_at = now - timedelta(seconds=1)
        stale = await provider.fetch_token_market("mint")
    assert first
    assert any(item.stale for item in stale)

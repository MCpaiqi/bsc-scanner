import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
from fastapi.testclient import TestClient

from smms.api.app import app
from smms.ingestion import DataUnavailable
from smms.ingestion.market import DexScreenerMarketProvider
from smms.ingestion.market.service import MarketFeedService


def test_dashboard_is_served_as_the_application_home() -> None:
    response = TestClient(app).get("/")

    assert response.status_code == 200
    assert "BSC Meme Radar" in response.text
    assert "只读情报台" in response.text


def test_demo_feed_is_explicit_and_does_not_need_external_network() -> None:
    response = TestClient(app).get("/api/bsc/tokens?demo=true&limit=2")

    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "demo"
    assert payload["demo"] is True
    assert len(payload["tokens"]) == 2


async def test_bsc_market_feed_uses_the_most_liquid_pair_per_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token-profiles/latest/v1":
            return httpx.Response(
                200,
                json=[
                    {
                        "chainId": "bsc",
                        "tokenAddress": "0xToken",
                        "icon": "https://cdn.example/token.png",
                        "description": "社区代币",
                        "links": [{"type": "twitter", "url": "https://x.com/token"}],
                    },
                    {"chainId": "solana", "tokenAddress": "ignored"},
                ],
            )
        if request.url.path == "/tokens/v1/bsc/0xToken":
            pairs = [
                {
                    "chainId": "bsc",
                    "dexId": "pancakeswap",
                    "pairAddress": "0xThinPair",
                    "baseToken": {"address": "0xToken", "name": "Radar", "symbol": "RDR"},
                    "quoteToken": {"symbol": "WBNB"},
                    "priceUsd": "0.001",
                    "liquidity": {"usd": 100},
                },
                {
                    "chainId": "bsc",
                    "dexId": "pancakeswap",
                    "pairAddress": "0xDeepPair",
                    "baseToken": {"address": "0xToken", "name": "Radar", "symbol": "RDR"},
                    "quoteToken": {"symbol": "USDT"},
                    "priceUsd": "0.002",
                    "marketCap": 250000,
                    "liquidity": {"usd": 42000},
                    "volume": {"m5": 1500, "h1": 7200, "h24": 91000},
                    "priceChange": {"m5": 4.2, "h1": -1.5, "h24": 38.1},
                    "txns": {"m5": {"buys": 18, "sells": 7}, "h1": {"buys": 80, "sells": 44}},
                    "pairCreatedAt": 1789000000000,
                    "url": "https://dexscreener.com/bsc/0xDeepPair",
                },
            ]
            return httpx.Response(200, content=json.dumps(pairs))
        return httpx.Response(404)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://api.dexscreener.com"
    ) as client:
        result = await DexScreenerMarketProvider(client=client).discover_latest_tokens(limit=12)

    assert result["source"] == "dexscreener"
    assert result["chain"] == "bsc"
    assert len(result["tokens"]) == 1
    token = result["tokens"][0]
    assert token["pair_address"] == "0xDeepPair"
    assert token["market_cap_usd"] == 250000.0
    assert token["txns_m5"] == {"buys": 18, "sells": 7}
    assert token["image_url"] == "https://cdn.example/token.png"
    assert token["socials"][0]["type"] == "twitter"


async def test_bsc_market_feed_rejects_malformed_nested_data() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token-profiles/latest/v1":
            return httpx.Response(
                200,
                json=[{"chainId": "bsc", "tokenAddress": "0xToken"}],
            )
        return httpx.Response(
            200,
            json=[{"chainId": "bsc", "baseToken": "invalid", "liquidity": []}],
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://api.dexscreener.com"
    ) as client:
        provider = DexScreenerMarketProvider(client=client)
        try:
            await provider.discover_latest_tokens(limit=12)
        except DataUnavailable as exc:
            assert "invalid" in str(exc).lower()
        else:
            raise AssertionError("malformed provider data must be rejected")


async def test_market_feed_service_reuses_recent_snapshot() -> None:
    class Provider:
        def __init__(self) -> None:
            self.calls = 0

        async def discover_latest_tokens(self, *, limit: int = 18) -> dict[str, object]:
            self.calls += 1
            return {"tokens": list(range(limit)), "source": "test"}

    now = datetime(2026, 9, 10, tzinfo=UTC)
    provider = Provider()
    service = MarketFeedService(provider=provider, ttl=timedelta(seconds=20), clock=lambda: now)

    first = await service.latest_tokens(limit=2)
    second = await service.latest_tokens(limit=2)

    assert first == second
    assert provider.calls == 1


async def test_market_feed_service_coalesces_concurrent_requests() -> None:
    class Provider:
        def __init__(self) -> None:
            self.calls = 0

        async def discover_latest_tokens(self, *, limit: int = 18) -> dict[str, object]:
            self.calls += 1
            await asyncio.sleep(0.01)
            return {"tokens": list(range(limit)), "source": "test"}

    provider = Provider()
    service = MarketFeedService(provider=provider)

    results = await asyncio.gather(*(service.latest_tokens(limit=3) for _ in range(3)))

    assert all(result == results[0] for result in results)
    assert provider.calls == 1

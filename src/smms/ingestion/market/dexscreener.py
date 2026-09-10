"""DexScreener adapter for read-only BSC market discovery."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from smms.ingestion.provider import DataUnavailable

DEXSCREENER_BASE_URL = "https://api.dexscreener.com"


def _mapping(value: Any, *, field: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise DataUnavailable(f"DexScreener returned invalid {field}")
    return value


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _count(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _pair_liquidity(pair: dict[str, Any]) -> float:
    liquidity = _mapping(pair.get("liquidity"), field="pair liquidity")
    return _number(liquidity.get("usd")) or 0.0


def _pair_timestamp(value: Any) -> str | None:
    milliseconds = _number(value)
    if not milliseconds:
        return None
    try:
        return datetime.fromtimestamp(milliseconds / 1000, tz=UTC).isoformat()
    except (OverflowError, OSError, ValueError) as exc:
        raise DataUnavailable("DexScreener returned invalid pair creation time") from exc


def _normalise_pair(pair: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    base = _mapping(pair.get("baseToken"), field="base token")
    quote = _mapping(pair.get("quoteToken"), field="quote token")
    volume = _mapping(pair.get("volume"), field="volume")
    changes = _mapping(pair.get("priceChange"), field="price change")
    txns = _mapping(pair.get("txns"), field="transactions")
    info = _mapping(pair.get("info"), field="pair info")
    links = profile.get("links") or []
    if not isinstance(links, list):
        raise DataUnavailable("DexScreener returned invalid profile links")

    def transaction_window(name: str) -> dict[str, int]:
        window = _mapping(txns.get(name), field=f"{name} transactions")
        return {"buys": _count(window.get("buys")), "sells": _count(window.get("sells"))}

    market_cap = _number(pair.get("marketCap"))
    if market_cap is None:
        market_cap = _number(pair.get("fdv"))

    return {
        "chain": "bsc",
        "token_address": base.get("address") or profile.get("tokenAddress"),
        "pair_address": pair.get("pairAddress"),
        "name": base.get("name") or "Unknown token",
        "symbol": base.get("symbol") or "—",
        "quote_symbol": quote.get("symbol") or "—",
        "dex_id": pair.get("dexId") or "unknown",
        "price_usd": _number(pair.get("priceUsd")),
        "market_cap_usd": market_cap,
        "liquidity_usd": _pair_liquidity(pair),
        "volume_m5": _number(volume.get("m5")),
        "volume_h1": _number(volume.get("h1")),
        "volume_h24": _number(volume.get("h24")),
        "price_change_m5": _number(changes.get("m5")),
        "price_change_h1": _number(changes.get("h1")),
        "price_change_h24": _number(changes.get("h24")),
        "txns_m5": transaction_window("m5"),
        "txns_h1": transaction_window("h1"),
        "pair_created_at": _pair_timestamp(pair.get("pairCreatedAt")),
        "image_url": profile.get("icon") or info.get("imageUrl"),
        "description": profile.get("description"),
        "socials": [
            {"type": link.get("type") or "website", "url": link.get("url")}
            for link in links
            if isinstance(link, dict) and link.get("url")
        ],
        "dex_url": pair.get("url"),
    }


class DexScreenerMarketProvider:
    """Access DexScreener's latest-profile feed behind the provider boundary."""

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self.client = client

    async def discover_latest_tokens(self, *, limit: int = 18) -> dict[str, Any]:
        if not 1 <= limit <= 30:
            raise ValueError("limit must be between 1 and 30")

        owns_client = self.client is None
        active_client = self.client or httpx.AsyncClient(
            base_url=DEXSCREENER_BASE_URL,
            timeout=10,
            headers={"User-Agent": "smms-bsc-radar/0.1"},
        )
        try:
            profile_response = await active_client.get("/token-profiles/latest/v1")
            profile_response.raise_for_status()
            raw_profiles = profile_response.json()
            if not isinstance(raw_profiles, list):
                raise DataUnavailable("DexScreener returned an invalid token profile response")

            profiles: dict[str, dict[str, Any]] = {}
            addresses: list[str] = []
            for profile in raw_profiles:
                if not isinstance(profile, dict):
                    continue
                if str(profile.get("chainId", "")).lower() != "bsc":
                    continue
                address = str(profile.get("tokenAddress") or "").strip()
                key = address.lower()
                if not address or key in profiles:
                    continue
                profiles[key] = profile
                addresses.append(address)
                if len(addresses) >= limit:
                    break

            if not addresses:
                return self._response([])

            pair_response = await active_client.get(f"/tokens/v1/bsc/{','.join(addresses)}")
            pair_response.raise_for_status()
            raw_pairs = pair_response.json()
            if not isinstance(raw_pairs, list):
                raise DataUnavailable("DexScreener returned an invalid pair response")

            deepest_pairs: dict[str, dict[str, Any]] = {}
            for pair in raw_pairs:
                if not isinstance(pair, dict):
                    continue
                if str(pair.get("chainId", "")).lower() != "bsc":
                    continue
                base = _mapping(pair.get("baseToken"), field="base token")
                key = str(base.get("address") or "").lower()
                if key not in profiles:
                    continue
                current = deepest_pairs.get(key)
                if current is None or _pair_liquidity(pair) > _pair_liquidity(current):
                    deepest_pairs[key] = pair

            tokens = [
                _normalise_pair(deepest_pairs[key], profiles[key])
                for key in profiles
                if key in deepest_pairs
            ]
            tokens.sort(
                key=lambda token: (token["volume_h1"] or 0, token["liquidity_usd"] or 0),
                reverse=True,
            )
            return self._response(tokens)
        except DataUnavailable:
            raise
        except (httpx.HTTPError, TypeError, ValueError) as exc:
            raise DataUnavailable("BSC market feed is temporarily unavailable") from exc
        finally:
            if owns_client:
                await active_client.aclose()

    @staticmethod
    def _response(tokens: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "chain": "bsc",
            "source": "dexscreener",
            "demo": False,
            "updated_at": datetime.now(UTC).isoformat(),
            "tokens": tokens,
        }

"""DexScreener adapter for read-only BSC market discovery."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from smms.ingestion.provider import DataUnavailable

DEXSCREENER_BASE_URL = "https://api.dexscreener.com"
logger = logging.getLogger(__name__)
# Keep bounded provider diagnostics visible to the application logger and
# pytest's capture handler even when the parent ``smms`` logger is configured
# with a non-propagating structured handler.
logger.propagate = True


def _pair_request_context(addresses: list[str] | tuple[str, ...]) -> tuple[int, str]:
    """Keep provider diagnostics useful without emitting an unbounded address list."""
    return len(addresses), ",".join(str(address) for address in addresses[:3])


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

    def __init__(self, client: httpx.AsyncClient | None = None, *, base_url: str = DEXSCREENER_BASE_URL, timeout: float = 10, rate_limit_per_second: float = 5, cache_ttl_seconds: float = 30, api_key: str | None = None, targeted_batch_size: int = 30) -> None:
        self.client = client
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.cache_ttl_seconds = cache_ttl_seconds
        # Kept as a reserved constructor option for compatibility. The public
        # DexScreener feed does not use an API-key header.
        self.api_key = api_key
        if targeted_batch_size < 1:
            raise ValueError("targeted_batch_size must be positive")
        self.targeted_batch_size = int(targeted_batch_size)
        self._rate_interval = 1 / rate_limit_per_second if rate_limit_per_second > 0 else 0
        self._last_request = 0.0
        self._request_lock = asyncio.Lock()
        self._cache: dict[int, tuple[float, dict[str, Any]]] = {}
        self._pair_cache: dict[tuple[str, ...], tuple[float, dict[str, Any]]] = {}

    async def _throttle(self) -> None:
        async with self._request_lock:
            wait = self._rate_interval - (time.monotonic() - self._last_request)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = time.monotonic()

    async def discover_latest_tokens(self, *, limit: int = 18) -> dict[str, Any]:
        if not 1 <= limit <= 30:
            raise ValueError("limit must be between 1 and 30")

        cached = self._cache.get(limit)
        if cached and time.monotonic() - cached[0] < self.cache_ttl_seconds:
            return cached[1]
        owns_client = self.client is None
        active_client = self.client or httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout,
            headers={"User-Agent": "smms-bsc-radar/0.1"},
        )
        try:
            await self._throttle()
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
            payload = self._response(tokens)
            self._cache[limit] = (time.monotonic(), payload)
            return payload
        except DataUnavailable:
            raise
        except (httpx.HTTPError, TypeError, ValueError) as exc:
            raise DataUnavailable("BSC market feed is temporarily unavailable") from exc
        finally:
            if owns_client:
                await active_client.aclose()

    async def fetch_pairs(self, pair_addresses: list[str] | tuple[str, ...]) -> dict[str, Any]:
        """Lookup only chain-discovered BSC pairs; profiles are never canonical."""
        addresses = list(dict.fromkeys(str(address).strip() for address in pair_addresses if str(address).strip()))
        if not addresses:
            return self._response([])
        cache_key = tuple(sorted(address.lower() for address in addresses))
        cached = self._pair_cache.get(cache_key)
        if cached and time.monotonic() - cached[0] < self.cache_ttl_seconds:
            return cached[1]
        owns_client = self.client is None
        client = self.client or httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout, headers={"User-Agent": "smms-bsc-radar/0.1"})
        try:
            wanted = {address.lower() for address in addresses}
            tokens_by_address: dict[str, dict[str, Any]] = {}
            for offset in range(0, len(addresses), self.targeted_batch_size):
                chunk = addresses[offset : offset + self.targeted_batch_size]
                requested_pair_count, pair_address_sample = _pair_request_context(chunk)
                await self._throttle()
                response = None
                for attempt in range(2):
                    try:
                        response = await client.get(f"/latest/dex/pairs/bsc/{','.join(chunk)}")
                        response.raise_for_status()
                        break
                    except httpx.HTTPError as exc:
                        status = getattr(response, "status_code", None)
                        body = (getattr(response, "text", "") or "")[:1000]
                        category = (
                            "429" if status == 429 else "5xx" if status is not None and status >= 500
                            else "timeout" if isinstance(exc, httpx.TimeoutException) else "http_error"
                        )
                        logger.warning(
                            "dexscreener_targeted_fetch_failed category=%s status=%s requested_pair_count=%s pair_address_sample=%s response=%s attempt=%s",
                            category, status, requested_pair_count, pair_address_sample, body, attempt + 1,
                        )
                        if attempt:
                            raise
                        await asyncio.sleep(min(0.5, self._rate_interval or 0.1))
                if response is None:
                    raise DataUnavailable("BSC targeted market lookup returned no response")
                body = response.json()
                raw_pairs = body.get("pairs") if isinstance(body, dict) else None
                if not isinstance(raw_pairs, list):
                    logger.warning(
                        "dexscreener_targeted_fetch_invalid_response status=%s requested_pair_count=%s pair_address_sample=%s response=%s",
                        response.status_code,
                        requested_pair_count,
                        pair_address_sample,
                        (response.text or "")[:1000],
                    )
                    raise DataUnavailable("BSC targeted market lookup returned an invalid pair response")
                matched_before = len(tokens_by_address)
                for pair in raw_pairs:
                    if not isinstance(pair, dict):
                        continue
                    pair_address = str(pair.get("pairAddress", "")).lower()
                    if str(pair.get("chainId", "")).lower() != "bsc" or pair_address not in wanted:
                        continue
                    tokens_by_address.setdefault(pair_address, _normalise_pair(pair, {}))
                logger.info(
                    "dexscreener_targeted_fetch_result status=%s requested_pair_count=%s pair_address_sample=%s returned_pair_count=%s matched_pair_count=%s",
                    response.status_code, requested_pair_count, pair_address_sample, len(raw_pairs),
                    len(tokens_by_address) - matched_before,
                )
            tokens = list(tokens_by_address.values())
            result = self._response(tokens)
            self._pair_cache[cache_key] = (time.monotonic(), result)
            return result
        except (httpx.HTTPError, TypeError, ValueError) as exc:
            requested_pair_count, pair_address_sample = _pair_request_context(addresses)
            logger.warning(
                "dexscreener_targeted_fetch_unavailable requested_pair_count=%s pair_address_sample=%s error=%s",
                requested_pair_count, pair_address_sample, str(exc)[:1000],
            )
            raise DataUnavailable("BSC targeted market lookup is temporarily unavailable") from exc
        finally:
            if owns_client:
                await client.aclose()

    @staticmethod
    def _response(tokens: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "chain": "bsc",
            "source": "dexscreener",
            "demo": False,
            "updated_at": datetime.now(UTC).isoformat(),
            "tokens": tokens,
        }

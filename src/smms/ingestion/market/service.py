"""Shared cache and request coalescing for the public market feed."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol


class MarketProvider(Protocol):
    async def discover_latest_tokens(self, *, limit: int = 18) -> dict[str, Any]: ...


class MarketFeedService:
    def __init__(
        self,
        *,
        provider: MarketProvider,
        ttl: timedelta = timedelta(seconds=20),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.provider = provider
        self.ttl = ttl
        self.clock = clock or (lambda: datetime.now(UTC))
        self._cache: dict[int, tuple[datetime, dict[str, Any]]] = {}
        self._lock = asyncio.Lock()

    async def latest_tokens(self, *, limit: int = 18) -> dict[str, Any]:
        cached = self._cache.get(limit)
        if cached and self.clock() - cached[0] < self.ttl:
            return cached[1]

        async with self._lock:
            cached = self._cache.get(limit)
            if cached and self.clock() - cached[0] < self.ttl:
                return cached[1]
            snapshot = await self.provider.discover_latest_tokens(limit=limit)
            self._cache[limit] = (self.clock(), snapshot)
            return snapshot

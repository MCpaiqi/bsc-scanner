from copy import deepcopy

from .provider import DataUnavailable
from .types import RawSwap, RawToken, RawTransaction


class MockProvider:
    """Deterministic provider for offline tests; it never fabricates missing keys."""

    def __init__(
        self,
        *,
        tokens: dict[str, RawToken] | None = None,
        transactions: dict[str, list[RawTransaction]] | None = None,
        swaps: dict[str, list[RawSwap]] | None = None,
    ) -> None:
        self.tokens = tokens or {}
        self.transactions = transactions or {}
        self.swaps = swaps or {}

    async def get_token(self, token_address: str) -> RawToken:
        if token_address not in self.tokens:
            raise DataUnavailable(f"No token fixture for {token_address}")
        return deepcopy(self.tokens[token_address])

    async def get_transactions(
        self, address: str, *, before: str | None = None, limit: int = 100
    ) -> list[RawTransaction]:
        return deepcopy(self.transactions.get(address, []))[:limit]

    async def get_swaps(
        self, address: str, *, before: str | None = None, limit: int = 100
    ) -> list[RawSwap]:
        return deepcopy(self.swaps.get(address, []))[:limit]


from typing import Protocol

from .types import RawSwap, RawToken, RawTransaction


class DataUnavailable(RuntimeError):
    """Raised when a provider cannot supply authoritative data."""


class DataProvider(Protocol):
    async def get_token(self, token_address: str) -> RawToken: ...

    async def get_transactions(
        self, address: str, *, before: str | None = None, limit: int = 100
    ) -> list[RawTransaction]: ...

    async def get_swaps(
        self, address: str, *, before: str | None = None, limit: int = 100
    ) -> list[RawSwap]: ...


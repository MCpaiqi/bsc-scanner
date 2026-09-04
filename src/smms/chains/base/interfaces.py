from collections.abc import AsyncIterator, Sequence
from typing import Protocol, runtime_checkable

from .models import Block, ChainLog, ChainTransaction, TransactionReceipt


@runtime_checkable
class ChainProvider(Protocol):
    """Minimum read-only chain seam used by launchpad adapters."""

    chain: str
    chain_id: int

    async def get_latest_block_number(self) -> int: ...

    async def get_block(
        self, block_number: int, *, include_transactions: bool = False
    ) -> Block | None: ...

    async def get_transaction(self, transaction_hash: str) -> ChainTransaction | None: ...

    async def get_transaction_receipt(self, transaction_hash: str) -> TransactionReceipt | None: ...

    async def get_logs(
        self,
        *,
        from_block: int,
        to_block: int,
        address: str | None = None,
        topics: Sequence[str] | None = None,
    ) -> list[ChainLog]: ...


@runtime_checkable
class ChainSubscription(Protocol):
    """Optional live stream seam; implementations may use WebSocket or another transport."""

    def subscribe_logs(
        self, *, address: str | None = None, topics: Sequence[str] | None = None
    ) -> AsyncIterator[ChainLog]: ...

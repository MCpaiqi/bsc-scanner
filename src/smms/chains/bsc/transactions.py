"""Transaction-context based trader resolution for BSC swaps."""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass

from smms.chains.base import ChainProvider, ChainTransaction
from smms.chains.constants import normalize_address

ZERO_ADDRESS = "0x" + "0" * 40


@dataclass(frozen=True)
class TransactionContext:
    tx_hash: str
    transaction_from: str | None
    transaction_to: str | None
    input_data: str | None
    resolution: str
    confidence: str
    error: str | None = None


class TransactionContextResolver:
    """Resolve one tx once and retain bounded, expiring context."""

    def __init__(
        self,
        provider: ChainProvider | None = None,
        *,
        ttl_seconds: float = 300,
        max_entries: int = 10_000,
        known_system_addresses: set[str] | None = None,
    ) -> None:
        self.provider = provider
        self.ttl_seconds = max(0.0, float(ttl_seconds))
        self.max_entries = max(1, int(max_entries))
        self.known_system_addresses = {
            normalize_address(address, chain="bsc") for address in (known_system_addresses or set())
        }
        self.known_system_addresses.add(ZERO_ADDRESS)
        self._cache: OrderedDict[str, tuple[float, TransactionContext]] = OrderedDict()
        self.resolved = 0
        self.failed = 0

    def add_system_addresses(self, addresses: set[str]) -> None:
        self.known_system_addresses.update(
            normalize_address(address, chain="bsc") for address in addresses if address
        )

    def is_system_address(self, address: str | None) -> bool:
        if not address:
            return False
        try:
            return normalize_address(address, chain="bsc") in self.known_system_addresses
        except (TypeError, ValueError):
            return False

    async def resolve(self, tx_hash: str, *, swap_sender: str | None = None, swap_recipient: str | None = None) -> TransactionContext:
        key = str(tx_hash).lower()
        now = time.monotonic()
        cached = self._cache.get(key)
        if cached is not None and (self.ttl_seconds == 0 or now - cached[0] <= self.ttl_seconds):
            self._cache.move_to_end(key)
            return cached[1]
        if self.provider is None:
            context = TransactionContext(
                tx_hash=key,
                transaction_from=None,
                transaction_to=None,
                input_data=None,
                resolution="UNRESOLVED",
                confidence="UNKNOWN",
                error="provider_unavailable",
            )
            self.failed += 1
            self._put(key, context)
            return context
        try:
            tx = await self.provider.get_transaction(key)
            context = self._from_transaction(tx, key, swap_sender=swap_sender, swap_recipient=swap_recipient)
            self.resolved += 1 if context.transaction_from else 0
            self.failed += 0 if context.transaction_from else 1
        except Exception as exc:  # noqa: BLE001 - unknown is safer than a guessed wallet
            context = TransactionContext(
                tx_hash=key,
                transaction_from=None,
                transaction_to=None,
                input_data=None,
                resolution="UNRESOLVED",
                confidence="UNKNOWN",
                error=type(exc).__name__,
            )
            self.failed += 1
        self._put(key, context)
        return context

    def _from_transaction(self, tx: ChainTransaction | None, key: str, *, swap_sender: str | None, swap_recipient: str | None) -> TransactionContext:
        if tx is None or not tx.from_address:
            return TransactionContext(key, None, getattr(tx, "to_address", None), getattr(tx, "input_data", None), "UNRESOLVED", "UNKNOWN", "not_found")
        sender = normalize_address(tx.from_address, chain="bsc")
        target = normalize_address(tx.to_address, chain="bsc") if tx.to_address else None
        if sender in self.known_system_addresses:
            return TransactionContext(key, sender, target, tx.input_data, "CONTRACT_CALLER", "LOW")
        resolution = "ROUTER_TX_FROM" if swap_sender and target and normalize_address(swap_sender, chain="bsc") != sender else "DIRECT_TX_FROM"
        confidence = "MEDIUM" if resolution == "ROUTER_TX_FROM" else "HIGH"
        return TransactionContext(key, sender, target, tx.input_data, resolution, confidence)

    def _put(self, key: str, context: TransactionContext) -> None:
        self._cache[key] = (time.monotonic(), context)
        self._cache.move_to_end(key)
        while len(self._cache) > self.max_entries:
            self._cache.popitem(last=False)


__all__ = ["TransactionContext", "TransactionContextResolver"]

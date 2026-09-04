"""Idempotent repositories for chain event identities."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from smms.db.models import TokenLifecycleEvent, TradeEvent


def _merge_values(target: Any, values: dict[str, Any]) -> None:
    for key, value in values.items():
        if value is not None and hasattr(type(target), key):
            setattr(target, key, value)


class TradeEventRepository:
    """Persist canonical trades by ``(chain, tx_hash, event_index)``."""

    def __init__(self, session: Session):
        self.session = session

    def get_by_identity(
        self, chain: str, tx_hash: str, event_index: int
    ) -> TradeEvent | None:
        return self.session.scalar(
            select(TradeEvent).where(
                TradeEvent.chain == chain,
                TradeEvent.tx_hash == tx_hash,
                TradeEvent.event_index == event_index,
            )
        )

    def add_idempotent(self, event: TradeEvent) -> TradeEvent:
        existing = self.get_by_identity(event.chain, event.tx_hash, event.event_index)
        if existing is None:
            try:
                with self.session.begin_nested():
                    self.session.add(event)
                    self.session.flush()
                existing = event
            except IntegrityError:
                existing = self.get_by_identity(event.chain, event.tx_hash, event.event_index)
                if existing is None:
                    raise
        if existing is not event:
            _merge_values(
                existing,
                {
                    "launchpad": event.launchpad,
                    "token_address": event.token_address,
                    "trader_address": event.trader_address,
                    "block_number": event.block_number,
                    "timestamp": event.timestamp,
                    "side": event.side,
                    "token_amount": event.token_amount,
                    "native_amount": event.native_amount,
                    "price": event.price,
                    "market_cap": event.market_cap,
                    "liquidity": event.liquidity,
                    "source": event.source,
                    "metadata_json": event.metadata_json,
                },
            )
        return existing


class TokenLifecycleEventRepository:
    """Persist lifecycle logs by ``(chain, tx_hash, event_index)``."""

    def __init__(self, session: Session):
        self.session = session

    def get_by_identity(
        self, chain: str, tx_hash: str, event_index: int
    ) -> TokenLifecycleEvent | None:
        return self.session.scalar(
            select(TokenLifecycleEvent).where(
                TokenLifecycleEvent.chain == chain,
                TokenLifecycleEvent.tx_hash == tx_hash,
                TokenLifecycleEvent.event_index == event_index,
            )
        )

    def add_idempotent(self, event: TokenLifecycleEvent) -> TokenLifecycleEvent:
        if not event.tx_hash:
            raise ValueError("lifecycle event tx_hash is required for idempotency")
        existing = self.get_by_identity(event.chain, event.tx_hash, event.event_index)
        if existing is None:
            try:
                with self.session.begin_nested():
                    self.session.add(event)
                    self.session.flush()
                existing = event
            except IntegrityError:
                existing = self.get_by_identity(event.chain, event.tx_hash, event.event_index)
                if existing is None:
                    raise
        if existing is not event:
            _merge_values(
                existing,
                {
                    "launchpad": event.launchpad,
                    "token_address": event.token_address,
                    "event_type": event.event_type,
                    "timestamp": event.timestamp,
                    "block_number": event.block_number,
                    "payload_json": event.payload_json,
                },
            )
        return existing

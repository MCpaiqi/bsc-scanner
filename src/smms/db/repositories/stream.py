"""Idempotent persistence for realtime chain logs and stream cursors."""

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from smms.db.models import RawChainEvent, StreamCheckpoint


def _event_identity(event: RawChainEvent) -> tuple[int, str, int, str | None]:
    return (
        event.chain_id,
        event.transaction_hash.lower(),
        event.log_index,
        event.block_hash.lower() if event.block_hash is not None else None,
    )


class RawChainEventRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_by_identity(self, chain_id: int, transaction_hash: str, log_index: int, block_hash: str | None = None) -> RawChainEvent | None:
        query = select(RawChainEvent).where(
            RawChainEvent.chain_id == chain_id,
            func.lower(RawChainEvent.transaction_hash) == transaction_hash.lower(),
            RawChainEvent.log_index == log_index,
        )
        if block_hash is None:
            query = query.where(RawChainEvent.block_hash.is_(None))
        else:
            query = query.where(func.lower(RawChainEvent.block_hash) == block_hash.lower())
        return self.session.scalar(query)

    def add_idempotent(self, event: RawChainEvent) -> RawChainEvent:
        event.transaction_hash = event.transaction_hash.lower()
        if event.block_hash is not None:
            event.block_hash = event.block_hash.lower()
        existing = self.get_by_identity(*_event_identity(event))
        if existing is None:
            try:
                with self.session.begin_nested():
                    self.session.add(event)
                    self.session.flush()
                return event
            except IntegrityError:
                existing = self.get_by_identity(*_event_identity(event))
                if existing is None:
                    raise
        _merge_event(existing, event)
        self.session.flush()
        return existing


class AsyncRawChainEventRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_identity(self, chain_id: int, transaction_hash: str, log_index: int, block_hash: str | None = None) -> RawChainEvent | None:
        query = select(RawChainEvent).where(
            RawChainEvent.chain_id == chain_id,
            func.lower(RawChainEvent.transaction_hash) == transaction_hash.lower(),
            RawChainEvent.log_index == log_index,
        )
        if block_hash is None:
            query = query.where(RawChainEvent.block_hash.is_(None))
        else:
            query = query.where(func.lower(RawChainEvent.block_hash) == block_hash.lower())
        return await self.session.scalar(query)

    async def add_idempotent(self, event: RawChainEvent) -> RawChainEvent:
        event.transaction_hash = event.transaction_hash.lower()
        if event.block_hash is not None:
            event.block_hash = event.block_hash.lower()
        existing = await self.get_by_identity(*_event_identity(event))
        if existing is None:
            try:
                async with self.session.begin_nested():
                    self.session.add(event)
                    await self.session.flush()
                return event
            except IntegrityError:
                existing = await self.get_by_identity(*_event_identity(event))
                if existing is None:
                    raise
        _merge_event(existing, event)
        await self.session.flush()
        return existing


def _merge_event(target: RawChainEvent, source: RawChainEvent) -> None:
    for name in (
        "block_number",
        "block_hash",
        "transaction_index",
        "contract_address",
        "topics",
        "data",
        "timestamp",
        "provider",
        "confirmation_status",
        "removed",
        "raw_payload_json",
    ):
        value = getattr(source, name)
        if value is not None:
            setattr(target, name, value)


class StreamCheckpointRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self, chain_id: int, provider: str, stream_name: str) -> StreamCheckpoint | None:
        return self.session.scalar(
            select(StreamCheckpoint).where(
                StreamCheckpoint.chain_id == chain_id,
                StreamCheckpoint.provider == provider,
                StreamCheckpoint.stream_name == stream_name,
            )
        )

    def upsert(
        self,
        *,
        chain_id: int,
        provider: str,
        stream_name: str,
        status: str,
        block_number: int | None = None,
        log_index: int | None = None,
        transaction_hash: str | None = None,
    ) -> StreamCheckpoint:
        checkpoint = self.get(chain_id, provider, stream_name)
        if checkpoint is None:
            checkpoint = StreamCheckpoint(
                chain_id=chain_id,
                provider=provider,
                stream_name=stream_name,
            )
            try:
                with self.session.begin_nested():
                    self.session.add(checkpoint)
                    self.session.flush()
            except IntegrityError:
                checkpoint = self.get(chain_id, provider, stream_name)
                if checkpoint is None:
                    raise
        _merge_checkpoint(
            checkpoint,
            status=status,
            block_number=block_number,
            log_index=log_index,
            transaction_hash=transaction_hash,
        )
        self.session.flush()
        return checkpoint


class AsyncStreamCheckpointRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, chain_id: int, provider: str, stream_name: str) -> StreamCheckpoint | None:
        return await self.session.scalar(
            select(StreamCheckpoint).where(
                StreamCheckpoint.chain_id == chain_id,
                StreamCheckpoint.provider == provider,
                StreamCheckpoint.stream_name == stream_name,
            )
        )

    async def list_prefix(self, chain_id: int, provider: str, stream_prefix: str) -> list[StreamCheckpoint]:
        result = await self.session.scalars(
            select(StreamCheckpoint).where(
                StreamCheckpoint.chain_id == chain_id,
                StreamCheckpoint.provider == provider,
                StreamCheckpoint.stream_name.like(f"{stream_prefix}%"),
            )
        )
        return list(result.all())

    async def get_safest(self, chain_id: int, provider: str, stream_prefix: str) -> StreamCheckpoint | None:
        rows = [row for row in await self.list_prefix(chain_id, provider, stream_prefix) if row.last_processed_block is not None]
        return min(rows, key=lambda row: (row.last_processed_block, row.last_processed_log or -1), default=None)

    async def upsert(
        self,
        *,
        chain_id: int,
        provider: str,
        stream_name: str,
        status: str,
        block_number: int | None = None,
        log_index: int | None = None,
        transaction_hash: str | None = None,
    ) -> StreamCheckpoint:
        checkpoint = await self.get(chain_id, provider, stream_name)
        if checkpoint is None:
            checkpoint = StreamCheckpoint(
                chain_id=chain_id,
                provider=provider,
                stream_name=stream_name,
            )
            try:
                async with self.session.begin_nested():
                    self.session.add(checkpoint)
                    await self.session.flush()
            except IntegrityError:
                checkpoint = await self.get(chain_id, provider, stream_name)
                if checkpoint is None:
                    raise
        _merge_checkpoint(
            checkpoint,
            status=status,
            block_number=block_number,
            log_index=log_index,
            transaction_hash=transaction_hash,
        )
        await self.session.flush()
        return checkpoint


def _merge_checkpoint(
    checkpoint: StreamCheckpoint,
    *,
    status: str,
    block_number: int | None,
    log_index: int | None,
    transaction_hash: str | None,
) -> None:
    checkpoint.status = status
    checkpoint.updated_at = datetime.now(UTC)
    if block_number is None:
        return
    current = checkpoint.last_processed_block
    if current is None or block_number > current:
        checkpoint.last_processed_block = block_number
        checkpoint.last_processed_log = log_index
        checkpoint.last_processed_transaction_hash = transaction_hash
    elif block_number == current and (
        checkpoint.last_processed_log is None or (log_index is not None and log_index >= checkpoint.last_processed_log)
    ):
        checkpoint.last_processed_log = log_index
        checkpoint.last_processed_transaction_hash = transaction_hash

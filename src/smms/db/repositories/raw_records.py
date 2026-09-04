import json
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from smms.db.models import RawChainRecord


class AsyncRawChainRecordRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def add_idempotent(
        self,
        provider: str,
        record_type: str,
        external_id: str,
        payload: dict[str, Any],
    ) -> RawChainRecord:
        record = await self.session.scalar(
            select(RawChainRecord).where(
                RawChainRecord.provider == provider,
                RawChainRecord.record_type == record_type,
                RawChainRecord.external_id == external_id,
            )
        )
        checksum = sha256(
            json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode()
        ).hexdigest()
        if record is None:
            record = RawChainRecord(
                provider=provider,
                record_type=record_type,
                external_id=external_id,
                fetched_at=datetime.now(UTC),
                checksum=checksum,
                payload_json=payload,
            )
            try:
                async with self.session.begin_nested():
                    self.session.add(record)
                    await self.session.flush()
            except IntegrityError:
                record = await self.session.scalar(
                    select(RawChainRecord).where(
                        RawChainRecord.provider == provider,
                        RawChainRecord.record_type == record_type,
                        RawChainRecord.external_id == external_id,
                    )
                )
                if record is None:
                    raise
        return record


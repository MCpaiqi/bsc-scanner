from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from smms.db.repositories import AsyncRawChainRecordRepository
from smms.ingestion.provider import DataProvider
from smms.normalization import normalize_token, normalize_transaction


class IngestionService:
    """Persist authoritative provider payloads exactly once and return normalized views."""

    def __init__(self, provider: DataProvider, session: AsyncSession):
        self.provider = provider
        self.session = session

    async def fetch_token(self, token_address: str) -> dict[str, Any]:
        raw = await self.provider.get_token(token_address)
        await AsyncRawChainRecordRepository(self.session).add_idempotent(
            "provider", "token", token_address, dict(raw)
        )
        await self.session.flush()
        return normalize_token(raw)

    async def fetch_transactions(self, address: str, limit: int = 100) -> list[dict[str, Any]]:
        raw_records = await self.provider.get_transactions(address, limit=limit)
        normalized: list[dict[str, Any]] = []
        for raw in raw_records:
            signature = raw.get("signature")
            if not signature:
                continue
            await AsyncRawChainRecordRepository(self.session).add_idempotent(
                "provider", "transaction", signature, dict(raw)
            )
            normalized.append(normalize_transaction(raw))
        await self.session.flush()
        return normalized

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from smms.db.models import Token, Wallet, WalletTransaction


class AsyncTokenRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_address(self, address: str, chain: str = "solana") -> Token | None:
        return await self.session.scalar(
            select(Token).where(Token.address == address, Token.chain == chain)
        )

    async def upsert(self, address: str, chain: str = "solana", **values: Any) -> Token:
        token = await self.get_by_address(address, chain)
        if token is None:
            token = Token(address=address, chain=chain)
            self.session.add(token)
        for key, value in values.items():
            if hasattr(Token, key) and value is not None:
                setattr(token, key, value)
        await self.session.flush()
        return token


class AsyncWalletRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_address(self, address: str, chain: str = "solana") -> Wallet | None:
        return await self.session.scalar(
            select(Wallet).where(Wallet.address == address, Wallet.chain == chain)
        )

    async def upsert(self, address: str, chain: str = "solana", **values: Any) -> Wallet:
        wallet = await self.get_by_address(address, chain)
        if wallet is None:
            wallet = Wallet(address=address, chain=chain)
            self.session.add(wallet)
        for key, value in values.items():
            if hasattr(Wallet, key) and value is not None:
                setattr(wallet, key, value)
        await self.session.flush()
        return wallet


class AsyncWalletTransactionRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def add_idempotent(
        self,
        wallet_id: int,
        signature: str,
        timestamp: datetime,
        **values: Any,
    ) -> WalletTransaction:
        transaction = await self.session.scalar(
            select(WalletTransaction).where(
                WalletTransaction.wallet_id == wallet_id,
                WalletTransaction.signature == signature,
            )
        )
        if transaction is None:
            transaction = WalletTransaction(
                wallet_id=wallet_id,
                signature=signature,
                timestamp=timestamp,
                **values,
            )
            self.session.add(transaction)
        await self.session.flush()
        return transaction

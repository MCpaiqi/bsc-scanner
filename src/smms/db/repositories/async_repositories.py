from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from smms.chains.constants import DEFAULT_CHAIN, normalize_address
from smms.db.models import Token, Wallet, WalletTransaction


class AsyncTokenRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_address(self, address: str, chain: str = DEFAULT_CHAIN) -> Token | None:
        address = normalize_address(address, chain=chain)
        return await self.session.scalar(
            select(Token).where(Token.address == address, Token.chain == chain)
        )

    async def upsert(self, address: str, chain: str = DEFAULT_CHAIN, **values: Any) -> Token:
        address = normalize_address(address, chain=chain)
        token = await self.get_by_address(address, chain)
        if token is None:
            token = Token(address=address, chain=chain)
            try:
                async with self.session.begin_nested():
                    self.session.add(token)
                    await self.session.flush()
            except IntegrityError:
                token = await self.get_by_address(address, chain)
                if token is None:
                    raise
        for key, value in values.items():
            if hasattr(Token, key) and value is not None:
                setattr(token, key, value)
        await self.session.flush()
        return token


class AsyncWalletRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_address(self, address: str, chain: str = DEFAULT_CHAIN) -> Wallet | None:
        address = normalize_address(address, chain=chain)
        return await self.session.scalar(
            select(Wallet).where(Wallet.address == address, Wallet.chain == chain)
        )

    async def upsert(self, address: str, chain: str = DEFAULT_CHAIN, **values: Any) -> Wallet:
        address = normalize_address(address, chain=chain)
        wallet = await self.get_by_address(address, chain)
        if wallet is None:
            wallet = Wallet(address=address, chain=chain)
            try:
                async with self.session.begin_nested():
                    self.session.add(wallet)
                    await self.session.flush()
            except IntegrityError:
                wallet = await self.get_by_address(address, chain)
                if wallet is None:
                    raise
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
            try:
                async with self.session.begin_nested():
                    self.session.add(transaction)
                    await self.session.flush()
            except IntegrityError:
                transaction = await self.session.scalar(
                    select(WalletTransaction).where(
                        WalletTransaction.wallet_id == wallet_id,
                        WalletTransaction.signature == signature,
                    )
                )
                if transaction is None:
                    raise
        for key, value in values.items():
            if hasattr(WalletTransaction, key) and value is not None:
                setattr(transaction, key, value)
        await self.session.flush()
        return transaction

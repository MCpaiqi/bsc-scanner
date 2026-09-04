from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from smms.db.models import Wallet


class WalletRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_by_address(self, address: str, chain: str = "solana") -> Wallet | None:
        return self.session.scalar(
            select(Wallet).where(Wallet.address == address, Wallet.chain == chain)
        )

    def upsert(self, address: str, chain: str = "solana", **values: Any) -> Wallet:
        wallet = self.get_by_address(address, chain)
        if wallet is None:
            wallet = Wallet(address=address, chain=chain)
            try:
                with self.session.begin_nested():
                    self.session.add(wallet)
                    self.session.flush()
            except IntegrityError:
                wallet = self.get_by_address(address, chain)
                if wallet is None:
                    raise
        for key, value in values.items():
            if hasattr(Wallet, key) and value is not None:
                setattr(wallet, key, value)
        return wallet

    def list(self, limit: int = 100, offset: int = 0) -> list[Wallet]:
        return list(
            self.session.scalars(
                select(Wallet).order_by(Wallet.id).offset(offset).limit(limit)
            )
        )

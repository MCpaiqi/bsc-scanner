from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from smms.db.models import WalletTransaction


class WalletTransactionRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_by_signature(self, wallet_id: int, signature: str) -> WalletTransaction | None:
        return self.session.scalar(
            select(WalletTransaction).where(
                WalletTransaction.wallet_id == wallet_id,
                WalletTransaction.signature == signature,
            )
        )

    def add_idempotent(
        self,
        wallet_id: int,
        signature: str,
        timestamp: datetime,
        **values: Any,
    ) -> WalletTransaction:
        transaction = self.get_by_signature(wallet_id, signature)
        if transaction is None:
            transaction = WalletTransaction(
                wallet_id=wallet_id,
                signature=signature,
                timestamp=timestamp,
            )
            try:
                with self.session.begin_nested():
                    self.session.add(transaction)
                    self.session.flush()
            except IntegrityError:
                transaction = self.get_by_signature(wallet_id, signature)
                if transaction is None:
                    raise
        for key, value in values.items():
            if hasattr(WalletTransaction, key) and value is not None:
                setattr(transaction, key, value)
        return transaction

    def list_for_wallet(self, wallet_id: int, limit: int = 100) -> list[WalletTransaction]:
        return list(
            self.session.scalars(
                select(WalletTransaction)
                .where(WalletTransaction.wallet_id == wallet_id)
                .order_by(WalletTransaction.timestamp.desc())
                .limit(limit)
            )
        )

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from smms.db.models import Token


class TokenRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_by_address(self, address: str, chain: str = "solana") -> Token | None:
        return self.session.scalar(
            select(Token).where(Token.address == address, Token.chain == chain)
        )

    def upsert(self, address: str, chain: str = "solana", **values: Any) -> Token:
        token = self.get_by_address(address, chain)
        if token is None:
            token = Token(address=address, chain=chain)
            self.session.add(token)
        for key, value in values.items():
            if hasattr(Token, key) and value is not None:
                setattr(token, key, value)
        return token

    def list(self, limit: int = 100, offset: int = 0) -> list[Token]:
        return list(
            self.session.scalars(
                select(Token).order_by(Token.id).offset(offset).limit(limit)
            )
        )


"""Point-in-time repositories for versioned intelligence snapshots."""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from smms.chains.constants import normalize_address
from smms.db.models import TokenSmartMoneySnapshot, WalletSmartMoneySnapshot
from smms.research.publication import publish_snapshot


class WalletSmartMoneySnapshotRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert(self, record: WalletSmartMoneySnapshot) -> WalletSmartMoneySnapshot:
        record.wallet_address = normalize_address(record.wallet_address, chain=record.chain)
        return publish_snapshot(self.session, record)

    def get_latest(
        self,
        wallet_address: str,
        *,
        chain: str,
        score_version: str,
        as_of: datetime,
    ) -> WalletSmartMoneySnapshot | None:
        return self.session.scalar(
            select(WalletSmartMoneySnapshot)
            .where(
                WalletSmartMoneySnapshot.chain == chain,
                WalletSmartMoneySnapshot.wallet_address == normalize_address(wallet_address, chain=chain),
                WalletSmartMoneySnapshot.score_version == score_version,
                WalletSmartMoneySnapshot.as_of <= as_of,
            )
            .order_by(WalletSmartMoneySnapshot.as_of.desc())
            .limit(1)
            .execution_options(populate_existing=True)
        )

    def list_latest(
        self, *, chain: str, score_version: str, as_of: datetime
    ) -> list[WalletSmartMoneySnapshot]:
        candidate = aliased(WalletSmartMoneySnapshot)
        latest_as_of = (
            select(func.max(candidate.as_of))
            .where(
                candidate.chain == WalletSmartMoneySnapshot.chain,
                candidate.wallet_address == WalletSmartMoneySnapshot.wallet_address,
                candidate.score_version == WalletSmartMoneySnapshot.score_version,
                candidate.as_of <= as_of,
            )
            .correlate(WalletSmartMoneySnapshot)
            .scalar_subquery()
        )
        return list(
            self.session.scalars(
                select(WalletSmartMoneySnapshot).where(
                    WalletSmartMoneySnapshot.chain == chain,
                    WalletSmartMoneySnapshot.score_version == score_version,
                    WalletSmartMoneySnapshot.as_of == latest_as_of,
                ).execution_options(populate_existing=True)
            ).all()
        )


class TokenSmartMoneySnapshotRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert(self, record: TokenSmartMoneySnapshot) -> TokenSmartMoneySnapshot:
        record.token_address = normalize_address(record.token_address, chain=record.chain)
        return publish_snapshot(self.session, record)

    def get_latest(
        self,
        token_address: str,
        *,
        chain: str,
        score_version: str,
        as_of: datetime,
    ) -> TokenSmartMoneySnapshot | None:
        return self.session.scalar(
            select(TokenSmartMoneySnapshot)
            .where(
                TokenSmartMoneySnapshot.chain == chain,
                TokenSmartMoneySnapshot.token_address == normalize_address(token_address, chain=chain),
                TokenSmartMoneySnapshot.score_version == score_version,
                TokenSmartMoneySnapshot.as_of <= as_of,
            )
            .order_by(TokenSmartMoneySnapshot.as_of.desc())
            .limit(1)
            .execution_options(populate_existing=True)
        )

    def list_latest_for_tokens(
        self,
        token_addresses: set[str],
        *,
        chain: str,
        score_version: str,
        as_of: datetime,
    ) -> list[TokenSmartMoneySnapshot]:
        if not token_addresses:
            return []
        candidate = aliased(TokenSmartMoneySnapshot)
        latest_as_of = (
            select(func.max(candidate.as_of))
            .where(
                candidate.chain == TokenSmartMoneySnapshot.chain,
                candidate.token_address == TokenSmartMoneySnapshot.token_address,
                candidate.score_version == TokenSmartMoneySnapshot.score_version,
                candidate.as_of <= as_of,
            )
            .correlate(TokenSmartMoneySnapshot)
            .scalar_subquery()
        )
        return list(
            self.session.scalars(
                select(TokenSmartMoneySnapshot).where(
                    TokenSmartMoneySnapshot.chain == chain,
                    TokenSmartMoneySnapshot.score_version == score_version,
                    TokenSmartMoneySnapshot.token_address.in_(
                        {normalize_address(address, chain=chain) for address in token_addresses}
                    ),
                    TokenSmartMoneySnapshot.as_of == latest_as_of,
                ).execution_options(populate_existing=True)
            ).all()
        )

"""Point-in-time repositories for versioned intelligence snapshots."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from smms.chains.constants import normalize_address
from smms.db.models import TokenSmartMoneySnapshot, WalletSmartMoneySnapshot


def _copy_values(target: Any, source: Any, names: tuple[str, ...]) -> None:
    for name in names:
        setattr(target, name, getattr(source, name))
    target.updated_at = datetime.now(UTC)


class WalletSmartMoneySnapshotRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert(self, record: WalletSmartMoneySnapshot) -> WalletSmartMoneySnapshot:
        record.wallet_address = normalize_address(record.wallet_address, chain=record.chain)
        existing = self.session.scalar(
            select(WalletSmartMoneySnapshot).where(
                WalletSmartMoneySnapshot.chain == record.chain,
                WalletSmartMoneySnapshot.wallet_address == record.wallet_address,
                WalletSmartMoneySnapshot.as_of == record.as_of,
                WalletSmartMoneySnapshot.score_version == record.score_version,
                WalletSmartMoneySnapshot.profile_version == record.profile_version,
            )
        )
        if existing is None:
            self.session.add(record)
            self.session.flush()
            return record
        _copy_values(
            existing,
            record,
            (
                "score", "confidence", "confidence_score", "sample_size",
                "data_completeness", "historical_coverage", "trade_quality",
                "profile_json", "components_json", "contributions_json",
            ),
        )
        return existing

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
                )
            ).all()
        )


class TokenSmartMoneySnapshotRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert(self, record: TokenSmartMoneySnapshot) -> TokenSmartMoneySnapshot:
        record.token_address = normalize_address(record.token_address, chain=record.chain)
        existing = self.session.scalar(
            select(TokenSmartMoneySnapshot).where(
                TokenSmartMoneySnapshot.chain == record.chain,
                TokenSmartMoneySnapshot.token_address == record.token_address,
                TokenSmartMoneySnapshot.as_of == record.as_of,
                TokenSmartMoneySnapshot.score_version == record.score_version,
            )
        )
        if existing is None:
            self.session.add(record)
            self.session.flush()
            return record
        _copy_values(
            existing,
            record,
            (
                "smart_wallet_count", "smart_money_buy_usd", "smart_money_sell_usd",
                "smart_money_net_flow", "early_smart_buy_count", "smart_money_score",
                "confidence", "confidence_score", "data_status", "top_smart_wallets_json",
            ),
        )
        return existing

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
                )
            ).all()
        )

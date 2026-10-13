"""Repositories for versioned token risk snapshots."""
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from smms.chains.constants import normalize_address
from smms.db.models import TokenRiskSnapshot


class TokenRiskSnapshotRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert(self, record: TokenRiskSnapshot) -> TokenRiskSnapshot:
        record.token_address = normalize_address(record.token_address, chain=record.chain)
        existing = self.session.scalar(
            select(TokenRiskSnapshot).where(
                TokenRiskSnapshot.chain == record.chain,
                TokenRiskSnapshot.token_address == record.token_address,
                TokenRiskSnapshot.as_of == record.as_of,
                TokenRiskSnapshot.risk_version == record.risk_version,
            )
        )
        if existing is None:
            self.session.add(record)
            self.session.flush()
            return record
        for name in ("contract_risk", "liquidity_risk", "holder_risk", "behavior_risk", "bot_probability", "wash_probability", "overall_risk", "confidence", "evidence_json", "dimensions_json"):
            setattr(existing, name, getattr(record, name))
        existing.updated_at = datetime.now(UTC)
        return existing

    def get_latest(self, token_address: str, *, chain: str, risk_version: str, as_of: datetime) -> TokenRiskSnapshot | None:
        return self.session.scalar(
            select(TokenRiskSnapshot)
            .where(TokenRiskSnapshot.chain == chain, TokenRiskSnapshot.token_address == normalize_address(token_address, chain=chain), TokenRiskSnapshot.risk_version == risk_version, TokenRiskSnapshot.as_of <= as_of)
            .order_by(TokenRiskSnapshot.as_of.desc()).limit(1)
        )

    def list_latest(self, *, chain: str, risk_version: str, as_of: datetime, limit: int = 100) -> list[TokenRiskSnapshot]:
        candidate = aliased(TokenRiskSnapshot)
        latest = select(func.max(candidate.as_of)).where(candidate.chain == TokenRiskSnapshot.chain, candidate.token_address == TokenRiskSnapshot.token_address, candidate.risk_version == TokenRiskSnapshot.risk_version, candidate.as_of <= as_of).correlate(TokenRiskSnapshot).scalar_subquery()
        return list(self.session.scalars(select(TokenRiskSnapshot).where(TokenRiskSnapshot.chain == chain, TokenRiskSnapshot.risk_version == risk_version, TokenRiskSnapshot.as_of == latest).order_by(TokenRiskSnapshot.overall_risk, TokenRiskSnapshot.token_address).limit(limit)).all())

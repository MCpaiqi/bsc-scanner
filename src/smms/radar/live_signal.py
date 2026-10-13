"""Point-in-time production signal materialization."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from smms.backtest.signal_engine import generate_signal
from smms.db.models import (
    EarlyBuyer,
    RadarCandidate,
    Signal,
    SignalType,
    Token,
    TokenRiskSnapshot,
)
from smms.observability import metrics

from .features import UNKNOWN, RadarFeatureSnapshot


def _decimal(value):
    try:
        return None if value in {None, UNKNOWN} else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class LiveSignalService:
    """Generate and persist one signal from the candidate's observable state."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def materialize(
        self, candidate: RadarCandidate, *, as_of: datetime | None = None
    ) -> Signal:
        cutoff = as_of or datetime.now(UTC)
        cutoff = cutoff.replace(tzinfo=UTC) if cutoff.tzinfo is None else cutoff.astimezone(UTC)
        token = self.session.get(Token, candidate.token_id)
        if token is None:
            raise ValueError(f"candidate {candidate.id} has no token")
        features = RadarFeatureSnapshot.from_mapping(candidate.features_json or {})
        buys = _decimal(features.buy_count_5m)
        sells = _decimal(features.sell_count_5m)
        market_score = (
            buys / (buys + sells) * 100
            if buys is not None and sells is not None and buys + sells > 0
            else None
        )
        risk_snapshot = (
            self.session.get(TokenRiskSnapshot, candidate.risk_snapshot_id)
            if candidate.risk_snapshot_id
            else None
        )
        if risk_snapshot is not None and _utc(risk_snapshot.as_of) > cutoff:
            raise ValueError("risk snapshot is newer than signal as_of")
        early_buyers = list(
            self.session.scalars(
                select(EarlyBuyer).where(
                    EarlyBuyer.chain == token.chain,
                    EarlyBuyer.token_address == token.address,
                    EarlyBuyer.first_buy_timestamp <= cutoff,
                )
            ).all()
        )
        generated = generate_signal(
            token.address,
            cutoff,
            market={
                "as_of": cutoff,
                "market_score": market_score,
                "price": features.price,
                "market_cap": features.market_cap,
                "liquidity": features.liquidity,
            },
            smart_money={
                "as_of": cutoff,
                "smart_money_score": features.smart_money_score,
            },
            early_buyers=[{"timestamp": row.first_buy_timestamp} for row in early_buyers],
            risk={
                "as_of": _utc(risk_snapshot.as_of) if risk_snapshot else cutoff,
                "overall_risk": features.risk,
            },
            model_version="signal-v1",
            config_version="phase-7.9.4",
        )
        existing = self.session.scalar(
            select(Signal).where(
                Signal.token_id == token.id,
                Signal.created_at == cutoff,
                Signal.model_version == generated.model_version,
            )
        )
        if existing is not None:
            candidate.latest_signal_id = existing.id
            return existing
        row = Signal(
            token_id=token.id,
            created_at=cutoff,
            signal_type=SignalType(generated.signal),
            signal_strength=generated.score,
            wallet_count=(
                int(features.known_smart_wallet_count)
                if features.known_smart_wallet_count not in {None, UNKNOWN}
                else 0
            ),
            consensus_score=generated.smart_money_score,
            token_score=generated.score,
            risk_score={"LOW": 100, "MEDIUM": 60, "HIGH": 20, "CRITICAL": 0}.get(
                generated.risk_level
            ),
            entry_price=generated.entry_price,
            market_cap=generated.entry_market_cap,
            confidence=generated.confidence * 100,
            smart_money_score=generated.smart_money_score,
            risk_level=generated.risk_level,
            market_score=generated.market_score,
            entry_market_cap=generated.entry_market_cap,
            entry_liquidity=generated.entry_liquidity,
            entry_reasons=list(generated.entry_reasons),
            risk_flags=list(generated.risk_flags),
            data_status=generated.data_status,
            model_version=generated.model_version,
            config_version=generated.config_version,
            config_hash=generated.config_hash,
            metadata_json={
                "feature_version": features.feature_version,
                "as_of": cutoff.isoformat(),
                "smart_snapshot_id": candidate.smart_snapshot_id,
                "risk_snapshot_id": candidate.risk_snapshot_id,
            },
        )
        self.session.add(row)
        self.session.flush()
        candidate.latest_signal_id = row.id
        metrics.increment("signals_total")
        return row


__all__ = ["LiveSignalService"]

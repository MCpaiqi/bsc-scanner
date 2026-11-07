"""Point-in-time production signal materialization."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from smms.backtest.signal_engine import SignalWeights, generate_signal
from smms.db.models import (
    RadarCandidate,
    Signal,
    SignalType,
    Token,
    TokenRiskSnapshot,
)
from smms.observability import metrics
from smms.research.lineage import manifest
from smms.research.publication import publish_snapshot
from smms.research.visibility import is_current_snapshot

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

    def __init__(self, session: Session, *, radar_config=None, weights=None, threshold=Decimal(50),
                 config_version="phase-7.9.4", reconstruction_policy=None) -> None:
        self.session = session
        self.radar_config = radar_config
        self.weights = weights or SignalWeights()
        self.threshold, self.config_version = Decimal(str(threshold)), config_version
        self.reconstruction_policy = reconstruction_policy

    def materialize(
        self, candidate: RadarCandidate, *, as_of: datetime | None = None
    ) -> Signal:
        cutoff = as_of or datetime.now(UTC)
        cutoff = cutoff.replace(tzinfo=UTC) if cutoff.tzinfo is None else cutoff.astimezone(UTC)
        token = self.session.get(Token, candidate.token_id)
        if token is None:
            raise ValueError(f"candidate {candidate.id} has no token")
        from .engine import RadarEngine
        from .projection import apply_projection, build_projection, radar_config_for

        engine = RadarEngine(self.session, self.radar_config or radar_config_for(self.session, candidate))
        projection = build_projection(engine, candidate, at=cutoff, reconstruction_policy=self.reconstruction_policy)
        features = RadarFeatureSnapshot.from_mapping(projection.features_json)
        buys = _decimal(features.buy_count_5m)
        sells = _decimal(features.sell_count_5m)
        market_score = (
            buys / (buys + sells) * 100
            if buys is not None and sells is not None and buys + sells > 0
            else None
        )
        risk_snapshot = (
            self.session.get(TokenRiskSnapshot, projection.risk_snapshot_id, populate_existing=True)
            if projection.risk_snapshot_id
            else None
        )
        if risk_snapshot is not None and not is_current_snapshot(self.session, risk_snapshot):
            risk_snapshot = None
        if risk_snapshot is not None and _utc(risk_snapshot.as_of) > cutoff:
            raise ValueError("risk snapshot is newer than signal as_of")
        early_buyers = projection.input_manifest.get("facts", {}).get("early_entry", {})
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
            early_buyers=early_buyers.get("entries", ()),
            risk={
                "as_of": _utc(risk_snapshot.as_of) if risk_snapshot else cutoff,
                "overall_risk": features.risk if risk_snapshot is not None else UNKNOWN,
            },
            model_version="signal-v1",
            config_version=self.config_version, weights=self.weights, threshold=self.threshold,
        )
        row = Signal(
            chain=token.chain, source_epoch=projection.source_epoch,
            snapshot_status=projection.snapshot_status,
            input_manifest=manifest(kind="signals", parameters={
                "candidate_id": candidate.id, "as_of": cutoff, "model_version": generated.model_version,
                "config_version": generated.config_version, "config_hash": generated.config_hash,
                "radar_config": engine.config.model_dump(mode="json"),
                "weights": {key: str(value) for key, value in self.weights.__dict__.items()}, "threshold": str(self.threshold),
                "reconstruction_policy": self.reconstruction_policy,
            }, facts={"features": projection.features_json}, dependencies=[{
                "kind": projection.__tablename__, "id": projection.id,
                "digest": projection.input_manifest["digest"], "source_epoch": projection.source_epoch,
            }], notes=[] if projection.snapshot_status == "VALID" else ["NO_CANONICAL_EVIDENCE"]),
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
            data_status=generated.data_status if projection.snapshot_status == "VALID" else "UNAVAILABLE",
            model_version=generated.model_version,
            config_version=generated.config_version,
            config_hash=generated.config_hash,
            metadata_json={
                "feature_version": features.feature_version,
                "as_of": cutoff.isoformat(),
                "candidate_id": candidate.id, "projection_id": projection.id,
                "smart_snapshot_id": projection.smart_snapshot_id,
                "risk_snapshot_id": projection.risk_snapshot_id,
                "early_entry_reason": early_buyers.get("reason"),
                "early_entry_epoch": projection.source_epoch,
            },
        )
        row = publish_snapshot(self.session, row)
        apply_projection(engine, candidate, projection)
        metrics.increment("signals_total")
        return row


__all__ = ["LiveSignalService"]

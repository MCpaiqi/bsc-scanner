"""Historical point-in-time dataset construction for research backtests.

The builder deliberately reads only facts whose observation timestamp/as-of is at
or before the signal timestamp.  It is usable with a synchronous SQLAlchemy
``Session`` (including SQLite fixtures) and can stream rows without retaining a
second copy in memory.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from smms.chains.constants import BSC_CHAIN_ID, validate_bsc_chain
from smms.db.models import (
    EarlyBuyer,
    MarketEvent,
    Pair,
    Token,
    TokenLifecycleEvent,
    TokenRiskSnapshot,
    TokenScore,
    TokenSmartMoneySnapshot,
    TradeEvent,
)

HORIZONS = (
    ("30s", timedelta(seconds=30)),
    ("1m", timedelta(minutes=1)),
    ("5m", timedelta(minutes=5)),
    ("15m", timedelta(minutes=15)),
    ("30m", timedelta(minutes=30)),
    ("1h", timedelta(hours=1)),
    ("4h", timedelta(hours=4)),
    ("24h", timedelta(hours=24)),
)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _json(value: Any) -> Any:
    if isinstance(value, datetime):
        return _utc(value).isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(v) for v in value]
    return value


@dataclass(frozen=True)
class PointInTimeBacktestRow:
    token: str
    signal_timestamp: datetime
    discovery_timestamp: datetime | None
    market: dict[str, Any]
    smart_money: dict[str, Any]
    early_buyer: dict[str, Any]
    risk: dict[str, Any]
    feature_price_history: tuple[dict[str, Any], ...]
    evaluation_price_path: tuple[dict[str, Any], ...]
    data_quality: dict[str, Any]
    model_version: str
    universe_reason: str = "DEX_PAIR_DISCOVERED"
    market_lifecycle: str = "DEX_PAIR_DISCOVERED"

    def as_dict(self) -> dict[str, Any]:
        return _json(
            {
                "token": self.token,
                "timestamp": self.signal_timestamp,
                "signal_timestamp": self.signal_timestamp,
                "discovery_timestamp": self.discovery_timestamp,
                "market": self.market,
                "smart_money": self.smart_money,
                "early_buyers": self.early_buyer.get("items", []),
                "early_buyer": {k: v for k, v in self.early_buyer.items() if k != "items"},
                "risk": self.risk,
                "feature_price_history": list(self.feature_price_history),
                "evaluation_price_path": list(self.evaluation_price_path),
                "market_lifecycle": self.market_lifecycle,
                "data_quality": self.data_quality,
                "model_version": self.model_version,
                "universe_reason": self.universe_reason,
            }
        )


@dataclass
class DatasetBuildReport:
    tokens_total: int = 0
    tokens_eligible: int = 0
    tokens_excluded: int = 0
    rows_generated: int = 0
    coverage: dict[str, int] = field(
        default_factory=lambda: {
            "price": 0,
            "market": 0,
            "smart_money": 0,
            "risk": 0,
            "early_buyer": 0,
        }
    )
    exclusion_reasons: Counter[str] = field(default_factory=Counter)
    build_seconds: float = 0.0
    rows_per_second: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "tokens_total": self.tokens_total,
            "tokens_eligible": self.tokens_eligible,
            "tokens_excluded": self.tokens_excluded,
            "rows_generated": self.rows_generated,
            "coverage": dict(self.coverage),
            "exclusion_reasons": dict(self.exclusion_reasons),
            "build_seconds": self.build_seconds,
            "rows_per_second": self.rows_per_second,
        }


class HistoricalDatasetBuilder:
    """Build reproducible point-in-time rows from normalized database facts."""

    def __init__(
        self,
        session: Session,
        *,
        chain: str = "bsc",
        model_version: str = "dataset-v2",
        score_version: str = "v2",
        risk_version: str = "risk-v1",
        chain_id: int = BSC_CHAIN_ID,
        config: dict[str, Any] | None = None,
    ) -> None:
        self.session, self.chain, self.model_version = (
            session,
            validate_bsc_chain(chain),
            model_version,
        )
        self.score_version, self.risk_version, self.chain_id = (
            score_version,
            risk_version,
            BSC_CHAIN_ID,
        )
        self.config = config or {}

    def config_hash(self) -> str:
        payload = {
            "chain": self.chain,
            "model_version": self.model_version,
            "score_version": self.score_version,
            "risk_version": self.risk_version,
            "chain_id": self.chain_id,
            "config": self.config,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode()
        ).hexdigest()[:16]

    def _lifecycle(self, token: Token) -> list[tuple[datetime, str]]:
        """Replay event-time observability, never current status or future pair existence.

        Creation timestamps denote public chain facts, not local ingestion time.
        Unknown pair timestamps are deliberately not evidence of observability.
        """
        states = []
        discovery = token.creation_timestamp or token.first_seen_at
        if token.launchpad and discovery:
            states.append((_utc(discovery), "LAUNCHPAD_DISCOVERED"))
        events = self.session.scalars(
            select(TokenLifecycleEvent).where(
                TokenLifecycleEvent.chain == self.chain,
                TokenLifecycleEvent.token_address == token.address.lower(),
            )
        ).all()
        for event in events:
            if event.launchpad and event.event_type == "CREATED":
                states.append((_utc(event.timestamp), "LAUNCHPAD_DISCOVERED"))
            elif event.event_type in {"MIGRATED", "MIGRATED_TO_DEX"}:
                states.append((_utc(event.timestamp), "MIGRATED_TO_DEX"))
        pairs = self.session.scalars(select(Pair).where(Pair.chain_id == self.chain_id)).all()
        for pair in pairs:
            if (
                token.address.lower() in {pair.token0_address.lower(), pair.token1_address.lower()}
                and pair.creation_timestamp is not None
            ):
                states.append((_utc(pair.creation_timestamp), "DEX_PAIR_DISCOVERED"))
        trades = self.session.scalars(
            select(TradeEvent).where(
                TradeEvent.chain == self.chain,
                TradeEvent.token_address == token.address.lower(),
                TradeEvent.launchpad.is_not(None),
            )
        ).all()
        states.extend((_utc(t.timestamp), "LAUNCHPAD_ACTIVE") for t in trades)
        return sorted(set(states))

    def _universe(self, start: datetime, end: datetime) -> tuple[list[Token], int, int]:
        tokens = list(self.session.scalars(select(Token).where(Token.chain == self.chain)).all())
        self._timelines = {t.id: self._lifecycle(t) for t in tokens}
        discovered = [
            t
            for t in tokens
            if any(ts <= end for ts, _ in self._timelines[t.id])
            or (
                (t.creation_timestamp or t.first_seen_at)
                and _utc(t.creation_timestamp or t.first_seen_at) <= end
            )
        ]
        eligible = [t for t in discovered if any(ts <= end for ts, _ in self._timelines[t.id])]
        return eligible, len(discovered), len(discovered) - len(eligible)

    @staticmethod
    def _event_payload(event: Any) -> dict[str, Any]:
        return {
            k: _json(getattr(event, k, None))
            for k in ("timestamp", "price", "market_cap", "liquidity", "usd_value", "source")
        }

    def _rows_for_token(
        self, token: Token, start: datetime, end: datetime, report: DatasetBuildReport
    ) -> Iterator[PointInTimeBacktestRow]:
        address = token.address.lower()
        evaluation_end = end + max(delta for _, delta in HORIZONS)
        trades = list(
            self.session.scalars(
                select(TradeEvent)
                .where(
                    TradeEvent.chain == self.chain,
                    TradeEvent.token_address == address,
                    TradeEvent.timestamp <= evaluation_end,
                )
                .order_by(TradeEvent.timestamp)
            ).all()
        )
        markets = list(
            self.session.scalars(
                select(MarketEvent)
                .where(
                    MarketEvent.chain_id == self.chain_id,
                    MarketEvent.token_address == address,
                    MarketEvent.timestamp <= evaluation_end,
                )
                .order_by(MarketEvent.timestamp)
            ).all()
        )
        snaps = list(
            self.session.scalars(
                select(TokenSmartMoneySnapshot)
                .where(
                    TokenSmartMoneySnapshot.chain == self.chain,
                    TokenSmartMoneySnapshot.token_address == address,
                    TokenSmartMoneySnapshot.score_version == self.score_version,
                    TokenSmartMoneySnapshot.as_of <= end,
                )
                .order_by(TokenSmartMoneySnapshot.as_of)
            ).all()
        )
        scores = list(
            self.session.scalars(
                select(TokenScore)
                .where(
                    TokenScore.token_id == token.id,
                    TokenScore.version == self.score_version,
                    TokenScore.timestamp <= end,
                )
                .order_by(TokenScore.timestamp)
            ).all()
        )
        risks = list(
            self.session.scalars(
                select(TokenRiskSnapshot)
                .where(
                    TokenRiskSnapshot.chain == self.chain,
                    TokenRiskSnapshot.token_address == address,
                    TokenRiskSnapshot.risk_version == self.risk_version,
                    TokenRiskSnapshot.as_of <= end,
                )
                .order_by(TokenRiskSnapshot.as_of)
            ).all()
        )
        early = list(
            self.session.scalars(
                select(EarlyBuyer)
                .where(
                    EarlyBuyer.chain == self.chain,
                    EarlyBuyer.token_address == address,
                    EarlyBuyer.first_buy_timestamp <= end,
                )
                .order_by(EarlyBuyer.first_buy_timestamp)
            ).all()
        )
        timeline = self._timelines[token.id]
        discovery = timeline[0][0]
        facts = sorted(
            {
                _utc(e.timestamp)
                for e in trades + markets
                if e.timestamp
                and start <= _utc(e.timestamp) <= end
                and (discovery is None or _utc(e.timestamp) >= _utc(discovery))
            }
        )
        facts = sorted(set(facts) | {ts for ts, _ in timeline if start <= ts <= end})
        if not facts:
            facts = [max(start, discovery)]
        prices = sorted(
            [self._event_payload(e) for e in trades if e.price is not None]
            + [self._event_payload(e) for e in markets if e.price is not None],
            key=lambda x: x["timestamp"],
        )
        for ts in facts:
            market_events = [e for e in markets if _utc(e.timestamp) <= ts]
            market = self._event_payload(market_events[-1]) if market_events else {}
            if not market:
                prior_trades = [
                    e for e in trades if _utc(e.timestamp) <= ts and e.price is not None
                ]
                if prior_trades:
                    market = self._event_payload(prior_trades[-1])
            market_available = bool(market)
            market["as_of"] = ts
            token_score = next((s for s in reversed(scores) if _utc(s.timestamp) <= ts), None)
            market.update(
                {
                    "score": token_score.market_score if token_score else None,
                    "market_cap": market.get("market_cap"),
                    "volume": market.get("volume"),
                    "momentum": token_score.volume_score if token_score else None,
                }
            )
            sm = next((s for s in reversed(snaps) if _utc(s.as_of) <= ts), None)
            risk = next((r for r in reversed(risks) if _utc(r.as_of) <= ts), None)
            buyers = [b for b in early if _utc(b.first_buy_timestamp) <= ts]
            smart = (
                {
                    "score": sm.smart_money_score,
                    "confidence": sm.confidence,
                    "smart_wallet_count": sm.smart_wallet_count,
                    "net_flow": sm.smart_money_net_flow,
                    "as_of": sm.as_of,
                }
                if sm
                else {"score": None, "as_of": None}
            )
            risk_data = (
                {
                    "level": risk.overall_risk,
                    "confidence": risk.confidence,
                    "bot_probability": risk.bot_probability,
                    "wash_probability": risk.wash_probability,
                    "as_of": risk.as_of,
                }
                if risk
                else {"level": "UNKNOWN", "confidence": None, "as_of": None}
            )
            quality = {
                "price": bool(market.get("price")),
                "market": market_available,
                "smart_money": sm is not None,
                "risk": risk is not None,
                "early_buyer": bool(buyers),
            }
            for key, ok in quality.items():
                report.coverage[key] += int(ok)
            reasons = []
            if not market.get("price"):
                reasons.append("NO_PRICE")
            if not market_available:
                reasons.append("NO_MARKET_HISTORY")
            if sm is None:
                reasons.append("NO_SMART_MONEY_HISTORY")
            if risk is None:
                reasons.append("NO_RISK_HISTORY")
            report.exclusion_reasons.update(reasons)
            report.rows_generated += 1
            yield PointInTimeBacktestRow(
                address,
                ts,
                discovery,
                market,
                smart,
                {
                    "count": len(buyers),
                    "quality": "OBSERVED" if buyers else "UNKNOWN",
                    "as_of": ts,
                    "items": [
                        {"first_buy_time": b.first_buy_timestamp, "wallet": b.wallet_address}
                        for b in buyers
                    ],
                },
                risk_data,
                tuple(p for p in prices if _utc(datetime.fromisoformat(p["timestamp"])) <= ts),
                tuple(
                    p
                    for p in prices
                    if ts < _utc(datetime.fromisoformat(p["timestamp"])) <= ts + timedelta(hours=24)
                ),
                quality,
                self.model_version,
                timeline[0][1],
                max(
                    (state for observed, state in timeline if observed <= ts),
                    key={
                        "LAUNCHPAD_DISCOVERED": 0,
                        "LAUNCHPAD_ACTIVE": 1,
                        "DEX_PAIR_DISCOVERED": 2,
                        "MIGRATED_TO_DEX": 3,
                    }.get,
                ),
            )

    def stream_build(
        self, start: datetime, end: datetime
    ) -> tuple[Iterator[PointInTimeBacktestRow], DatasetBuildReport]:
        start, end = _utc(start), _utc(end)
        if start > end:
            raise ValueError("dataset start must be before or equal to end")
        report = DatasetBuildReport()
        universe, total, excluded = self._universe(start, end)
        report.tokens_total = total
        report.tokens_eligible = len(universe)
        report.tokens_excluded = excluded
        if excluded:
            report.exclusion_reasons["NO_OBSERVABLE_MARKET"] += excluded

        def iterator() -> Iterator[PointInTimeBacktestRow]:
            for token in universe:
                yield from self._rows_for_token(token, start, end, report)

        return iterator(), report

    def build(
        self, start: datetime, end: datetime
    ) -> tuple[list[PointInTimeBacktestRow], DatasetBuildReport]:
        started = time.perf_counter()
        stream, report = self.stream_build(start, end)
        rows = list(stream)
        report.tokens_excluded = report.tokens_total - report.tokens_eligible
        report.build_seconds = time.perf_counter() - started
        report.rows_per_second = (
            report.rows_generated / report.build_seconds if report.build_seconds else 0.0
        )
        return rows, report

    def materialize(
        self, start: datetime, end: datetime, *, dataset_version: str
    ) -> tuple[Any, DatasetBuildReport]:
        from smms.db.models import BacktestDataset, BacktestDatasetRow

        rows, report = self.build(start, end)
        dataset = BacktestDataset(
            dataset_version=dataset_version,
            period_from=_utc(start),
            period_to=_utc(end),
            chain=self.chain,
            config_hash=self.config_hash(),
            report_json=report.as_dict(),
            source_coverage_json=report.coverage,
        )
        self.session.add(dataset)
        self.session.flush()
        for row in rows:
            self.session.add(
                BacktestDatasetRow(
                    dataset_id=dataset.id,
                    token=row.token,
                    signal_timestamp=row.signal_timestamp,
                    discovery_timestamp=row.discovery_timestamp,
                    payload_json=row.as_dict(),
                )
            )
        self.session.commit()
        return dataset, report


def load_materialized_dataset(session: Session, dataset_id: int | str) -> list[dict[str, Any]]:
    """Load rows by numeric id or version for the production backtest path."""
    from smms.db.models import BacktestDataset, BacktestDatasetRow

    query = select(BacktestDataset)
    if str(dataset_id).isdigit():
        query = query.where(BacktestDataset.id == int(dataset_id))
    else:
        query = query.where(BacktestDataset.dataset_version == str(dataset_id))
    dataset = session.scalar(query)
    if dataset is None:
        raise LookupError(f"backtest dataset not found: {dataset_id}")
    rows = session.scalars(
        select(BacktestDatasetRow)
        .where(BacktestDatasetRow.dataset_id == dataset.id)
        .order_by(BacktestDatasetRow.signal_timestamp)
    ).all()
    return [row.payload_json for row in rows]


__all__ = [
    "DatasetBuildReport",
    "HistoricalDatasetBuilder",
    "PointInTimeBacktestRow",
    "load_materialized_dataset",
]

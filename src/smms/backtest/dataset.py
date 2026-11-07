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
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from smms.chains.constants import BSC_CHAIN_ID, validate_bsc_chain
from smms.db.facts import canonical_fact_conditions
from smms.db.models import (
    MarketEvent,
    Pair,
    Token,
    TokenLifecycleEvent,
    TokenRiskSnapshot,
    TokenSmartMoneySnapshot,
    TradeEvent,
)
from smms.evaluation import ENDPOINT_TOLERANCE
from smms.radar.forward_prices import ENTRY_MAX_AGE, load_usd_prices, select_entry_price
from smms.research.lineage import digest, json_value, manifest, row_values
from smms.research.publication import publish_snapshot
from smms.research.state import capture_epoch
from smms.smart_money.replay import load_replay_inputs

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
    evaluation_as_of: datetime | None = None

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
                "evaluation_as_of": self.evaluation_as_of,
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
        model_version: str = "dataset-v5",
        score_version: str = "v2",
        risk_version: str = "risk-v1",
        chain_id: int = BSC_CHAIN_ID,
        config: dict[str, Any] | None = None,
        evaluation_as_of: datetime | None = None,
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
        self.requested_evaluation_as_of = evaluation_as_of

    def parameters(self):
        return {"chain": self.chain, "model_version": self.model_version,
                "score_version": self.score_version, "risk_version": self.risk_version,
                "chain_id": self.chain_id, "config": self.config,
                "extraction_policy": "explicit-usd-frozen-pair-v1",
                "evaluation_as_of": getattr(self, "_evaluation_as_of", self.requested_evaluation_as_of)}

    def _remember(self, rows):
        for row in rows:
            self._source_facts[(row.__tablename__, row.id)] = row_values(row)

    def _depend(self, rows):
        for row in rows:
            self._dependencies[(row.__tablename__, row.id)] = {
                "kind": row.__tablename__, "id": row.id,
                "digest": (row.input_manifest or {}).get("digest"),
            }

    def _check_epoch(self):
        if capture_epoch(self.session, self.chain) != self._epoch:
            raise ValueError("canonical epoch changed during historical dataset extraction")

    def config_hash(self) -> str:
        payload = self.parameters()
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode()
        ).hexdigest()[:16]

    def _lifecycle(self, token: Token) -> list[tuple[datetime, str]]:
        """Replay event-time observability, never current status or future pair existence.

        Creation timestamps denote public chain facts, not local ingestion time.
        Unknown pair timestamps are deliberately not evidence of observability.
        """
        states = []
        # A mutable Token launchpad label is not canonical discovery evidence.
        events = self.session.scalars(
            select(TokenLifecycleEvent).where(
                TokenLifecycleEvent.chain == self.chain,
                TokenLifecycleEvent.token_address == token.address.lower(),
                TokenLifecycleEvent.timestamp <= self._signal_end,
                *canonical_fact_conditions(TokenLifecycleEvent),
            ).execution_options(populate_existing=True)
        ).all()
        self._remember(events)
        for event in events:
            if event.launchpad and event.event_type == "CREATED":
                states.append((_utc(event.timestamp), "LAUNCHPAD_DISCOVERED"))
            elif event.event_type in {"MIGRATED", "MIGRATED_TO_DEX"}:
                states.append((_utc(event.timestamp), "MIGRATED_TO_DEX"))
        pairs = self.session.scalars(select(Pair).where(
            Pair.chain_id == self.chain_id, *canonical_fact_conditions(Pair),
            Pair.creation_timestamp <= self._signal_end,
            or_(Pair.token0_address == token.address.lower(), Pair.token1_address == token.address.lower()),
        ).execution_options(populate_existing=True)).all()
        self._remember(pairs)
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
                TradeEvent.timestamp <= self._signal_end,
                *canonical_fact_conditions(TradeEvent),
            ).execution_options(populate_existing=True)
        ).all()
        self._remember(trades)
        states.extend((_utc(t.timestamp), "LAUNCHPAD_ACTIVE") for t in trades)
        return sorted(set(states))

    def _universe(self, start: datetime, end: datetime) -> tuple[list[Token], int, int]:
        tokens = list(self.session.scalars(select(Token).where(Token.chain == self.chain)
                                          .order_by(Token.id).execution_options(populate_existing=True)).all())
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

    def _rows_for_token(
        self, token: Token, start: datetime, end: datetime, report: DatasetBuildReport
    ) -> Iterator[PointInTimeBacktestRow]:
        address = token.address.lower()
        evaluation_end = self._evaluation_as_of
        sources = []
        for model, chain in ((TradeEvent, TradeEvent.chain == self.chain),
                             (MarketEvent, MarketEvent.chain_id == self.chain_id)):
            sources.extend(self.session.scalars(select(model).where(
                chain, model.token_address == address, model.timestamp <= evaluation_end,
                *canonical_fact_conditions(model),
            ).order_by(model.timestamp, model.id).execution_options(populate_existing=True)).all())
        self._remember(sources)
        snapshots = []
        for model, version in ((TokenSmartMoneySnapshot, TokenSmartMoneySnapshot.score_version == self.score_version),
                               (TokenRiskSnapshot, TokenRiskSnapshot.risk_version == self.risk_version)):
            snapshots.append(self.session.scalars(select(model).where(
                model.chain == self.chain, model.token_address == address, version, model.as_of <= end,
            ).order_by(model.as_of, model.id).execution_options(populate_existing=True)).all())
        snaps, risks = snapshots
        self._depend([*snaps, *risks])
        early = load_replay_inputs(self.session, address, as_of=end, chain=self.chain)
        # Membership is a prefix of first-buy order. Only export membership/time;
        # future position totals are deliberately not projected back to earlier rows.
        early_members = early.buyers(as_of=end)
        self._early_inputs[address] = early.input_manifest()
        timeline = self._timelines[token.id]
        discovery = timeline[0][0]
        facts = sorted({_utc(e.timestamp) for e in sources
                        if start <= _utc(e.timestamp) <= end and _utc(e.timestamp) >= discovery}
                       | {ts for ts, _ in timeline if start <= ts <= end})
        if not facts:
            facts = [max(start, discovery)]
        prices = load_usd_prices(self.session, [(address, start - ENTRY_MAX_AGE, evaluation_end)],
                                 as_of=evaluation_end).get(address, [])
        self._price_inputs[address] = [json_value({"timestamp": p.timestamp, "price": p.price,
                                                  "reference": p.reference, "provenance": dict(p.provenance)})
                                       for p in prices]
        for point in prices:
            proof = point.provenance
            if proof["model"] == "TokenMarketSnapshot":
                self._dependencies[("token_market_snapshots", proof["id"])] = {
                    "kind": "token_market_snapshots", "id": proof["id"], "digest": proof["input_digest"],
                }
        for ts in facts:
            self._check_epoch()
            entry = select_entry_price(prices, at=ts)
            proof = entry.provenance if entry else {}
            activity = [e for e in early.transactions if ts - timedelta(minutes=5) <= _utc(e["timestamp"]) <= ts]
            buy = sum(e.get("side") == "BUY" for e in activity)
            sell = sum(e.get("side") == "SELL" for e in activity)
            market = {
                "as_of": ts, "price": json_value(entry.price) if entry else None,
                "price_basis": "USD" if entry else "UNKNOWN",
                "pair_address": proof.get("pair_address"), "entry_source": dict(proof) if entry else None,
                "score": Decimal(buy * 100) / (buy + sell) if buy + sell else None,
                "market_cap": proof.get("market_cap_usd"), "liquidity": proof.get("liquidity_usd"),
                "volume": None, "momentum": None,
            }
            sm = next((s for s in reversed(snaps) if _utc(s.as_of) <= ts), None)
            risk = next((r for r in reversed(risks) if _utc(r.as_of) <= ts), None)
            buyers = [b for b in early_members if _utc(b.first_buy_time) <= ts]
            smart = ({"score": sm.smart_money_score, "confidence": sm.confidence,
                      "smart_wallet_count": sm.smart_wallet_count, "net_flow": sm.smart_money_net_flow,
                      "as_of": sm.as_of} if sm else {"score": None, "as_of": None})
            risk_data = ({"overall_risk": risk.overall_risk, "level": risk.overall_risk,
                          "confidence": risk.confidence, "bot_probability": risk.bot_probability,
                          "wash_probability": risk.wash_probability, "as_of": risk.as_of}
                         if risk else {"overall_risk": "UNKNOWN", "level": "UNKNOWN", "confidence": None, "as_of": None})
            quality = {"price": entry is not None, "market": entry is not None or bool(activity),
                       "smart_money": sm is not None, "risk": risk is not None, "early_buyer": bool(buyers)}
            for key, ok in quality.items():
                report.coverage[key] += int(ok)
            report.exclusion_reasons.update(reason for key, reason in (
                ("price", "NO_PRICE"), ("market", "NO_MARKET_HISTORY"),
                ("smart_money", "NO_SMART_MONEY_HISTORY"), ("risk", "NO_RISK_HISTORY"),
            ) if not quality[key])
            report.rows_generated += 1
            feature = (json_value({"timestamp": entry.timestamp, "price": entry.price,
                                   "reference": entry.reference, "provenance": dict(entry.provenance),
                                   "market_cap": market["market_cap"]}),) if entry else ()
            future = tuple(json_value({"timestamp": p.timestamp, "price": p.price,
                                       "reference": p.reference, "provenance": dict(p.provenance)})
                           for p in sorted(prices, key=lambda p: (p.timestamp, p.reference))
                           if entry and p.provenance["pair_address"] == proof["pair_address"]
                           and ts < p.timestamp <= ts + timedelta(hours=24) + ENDPOINT_TOLERANCE)
            yield PointInTimeBacktestRow(
                address, ts, discovery, market, smart,
                {"count": len(buyers), "quality": "OBSERVED" if buyers else "UNKNOWN", "as_of": ts,
                 "input_manifest": early.input_manifest(as_of=ts),
                 "items": [{"first_buy_time": b.first_buy_time, "wallet": b.wallet_address} for b in buyers]},
                risk_data, feature, future, quality, self.model_version, timeline[0][1],
                max((state for observed, state in timeline if observed <= ts),
                    key={"LAUNCHPAD_DISCOVERED": 0, "LAUNCHPAD_ACTIVE": 1,
                         "DEX_PAIR_DISCOVERED": 2, "MIGRATED_TO_DEX": 3}.get), evaluation_end,
            )

    def stream_build(
        self, start: datetime, end: datetime
    ) -> tuple[Iterator[PointInTimeBacktestRow], DatasetBuildReport]:
        start, end = _utc(start), _utc(end)
        if start > end:
            raise ValueError("dataset start must be before or equal to end")
        if self.model_version != "dataset-v5":
            raise ValueError("unsupported historical extraction recipe")
        read_at = datetime.now(UTC)
        self._evaluation_as_of = min(_utc(self.requested_evaluation_as_of or read_at), read_at,
                                     end + max(delta for _, delta in HORIZONS) + ENDPOINT_TOLERANCE)
        if self._evaluation_as_of < end:
            raise ValueError("evaluation cutoff cannot precede the signal period")
        self._epoch = capture_epoch(self.session, self.chain)
        self._signal_end = end
        self._source_facts, self._dependencies = {}, {}
        self._early_inputs, self._price_inputs = {}, {}
        report = DatasetBuildReport()
        universe, total, excluded = self._universe(start, end)
        report.tokens_total = total
        report.tokens_eligible = len(universe)
        report.tokens_excluded = excluded
        if excluded:
            report.exclusion_reasons["NO_OBSERVABLE_MARKET"] += excluded

        def iterator() -> Iterator[PointInTimeBacktestRow]:
            self._check_epoch()
            for token in universe:
                yield from self._rows_for_token(token, start, end, report)
            self._check_epoch()

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
        self, start: datetime, end: datetime, *, dataset_version: str, commit: bool = True,
        reconstruction_policy: str | None = None,
    ) -> tuple[Any, DatasetBuildReport]:
        from smms.db.models import BacktestDataset, BacktestDatasetRow

        rows, report = self.build(start, end)
        inputs = manifest(kind="backtest_datasets", parameters={**self.parameters(),
            "period_from": _utc(start), "period_to": _utc(end),
            "dataset_version": dataset_version, "reconstruction_policy": reconstruction_policy,
        }, facts={"sources": [{"kind": kind, "values": values}
                               for (kind, _), values in sorted(self._source_facts.items())],
                  "early_inputs": self._early_inputs, "price_inputs": self._price_inputs,
                  "universe": {str(k): v for k, v in self._timelines.items()},
                  "rows_digest": digest([row.as_dict() for row in rows])},
            dependencies=list(self._dependencies.values()))
        self._check_epoch()
        dataset = BacktestDataset(
            dataset_version=dataset_version,
            period_from=_utc(start),
            period_to=_utc(end),
            chain=self.chain,
            config_hash=self.config_hash(),
            report_json=report.as_dict(),
            source_coverage_json=report.coverage,
            source_epoch=self._epoch, snapshot_status="VALID", input_manifest=inputs,
        )
        published = publish_snapshot(self.session, dataset)
        if published is not dataset:
            if commit:
                self.session.commit()
            return published, report
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
        self.session.flush()
        if commit:
            self.session.commit()
        return dataset, report


def load_materialized_dataset(session: Session, dataset_id: int | str) -> list[dict[str, Any]]:
    """Load rows by numeric id or version for the production backtest path."""
    from smms.db.models import BacktestDataset, BacktestDatasetRow

    query = select(BacktestDataset).where(BacktestDataset.chain == "bsc").execution_options(populate_existing=True)
    if str(dataset_id).isdigit():
        query = query.where(BacktestDataset.id == int(dataset_id))
    else:
        query = query.where(BacktestDataset.dataset_version == str(dataset_id))
    dataset = session.scalar(query.order_by(BacktestDataset.research_revision.desc()).limit(1))
    if dataset is None:
        raise LookupError(f"backtest dataset not found: {dataset_id}")
    rows = session.scalars(
        select(BacktestDatasetRow)
        .where(BacktestDatasetRow.dataset_id == dataset.id)
        .order_by(BacktestDatasetRow.signal_timestamp)
        .execution_options(populate_existing=True)
    ).all()
    from smms.research.visibility import is_current_snapshot

    if not is_current_snapshot(session, dataset):
        raise LookupError(f"backtest dataset invalidated: {dataset_id}")
    return [deepcopy(row.payload_json) for row in rows]


__all__ = [
    "DatasetBuildReport",
    "HistoricalDatasetBuilder",
    "PointInTimeBacktestRow",
    "load_materialized_dataset",
]

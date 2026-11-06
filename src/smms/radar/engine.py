"""Transactional projection from canonical chain facts to radar candidates.

No network calls, signal generation, historical wallet scans or queue dispatch.
Callers own the transaction. Async adapters use AsyncSession.run_sync.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from smms.chains.constants import BSC_CHAIN_ID
from smms.db.facts import canonical_fact_conditions
from smms.db.models import (
    MarketEvent,
    Pair,
    RadarCandidate,
    RadarCandidateEvidence,
    RadarCandidateHistory,
    RawChainEvent,
    Signal,
    Token,
    TokenLifecycleEvent,
    TokenMarketSnapshot,
    TokenRiskSnapshot,
    TokenSmartMoneySnapshot,
    TradeEvent,
)
from smms.observability import metrics
from smms.research.lineage import row_values
from smms.research.visibility import is_current_snapshot
from smms.smart_money.replay import load_early_entries, usd_price

from .config import RadarConfig
from .domain import CandidateStatus as S
from .features import RadarFeatureSnapshot

UNKNOWN = "UNKNOWN"
SOURCES = {
    "pair": (Pair, "pair_id"),
    "lifecycle": (TokenLifecycleEvent, "lifecycle_id"),
    "trade": (TradeEvent, "trade_id"),
    "market": (MarketEvent, "market_id"),
}


def utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def normalized(chain, address):
    return address.lower() if chain == "bsc" else address


def metadata(row):
    return getattr(row, "metadata_json", None) or getattr(row, "payload_json", None) or {}


def observed(row):
    return getattr(row, "creation_timestamp", None) if isinstance(row, Pair) else row.timestamp


class RadarEngine:
    def __init__(self, session: Session, config: RadarConfig | None = None, *, chain_id=BSC_CHAIN_ID):
        self.session, self.config, self.chain_id = session, config or RadarConfig(), chain_id

    def raw_for(self, row, chain):
        if chain != "bsc":
            return None
        tx = row.creation_tx_hash if isinstance(row, Pair) else row.tx_hash
        if not tx:
            return None
        query = select(RawChainEvent).where(
            RawChainEvent.chain_id == self.chain_id,
            func.lower(RawChainEvent.transaction_hash) == tx.lower(),
        )
        row_block_hash = (getattr(row, "creation_block_hash", None) or getattr(row, "block_hash", None)
                          or metadata(row).get("block_hash"))
        if row_block_hash:
            query = query.where(func.lower(RawChainEvent.block_hash) == row_block_hash.lower())
        if not isinstance(row, Pair):
            query = query.where(RawChainEvent.log_index == row.event_index)
        else:
            index = metadata(row).get("log_index")
            if index is None:
                # Legacy Pair rows cannot disambiguate several factory logs in one tx.
                raws = list(self.session.scalars(query.execution_options(populate_existing=True)).all())
                return raws[0] if len(raws) == 1 else None
            query = query.where(RawChainEvent.log_index == index)
        return self.session.scalar(query.execution_options(populate_existing=True))

    def canonical(self, row, chain, proof=None):
        if row is None or observed(row) is None:
            return False
        if type(row) in {Pair, TradeEvent, MarketEvent, TokenLifecycleEvent}:
            current = self.session.scalar(select(type(row)).where(
                type(row).id == row.id, *canonical_fact_conditions(type(row)),
            ).execution_options(populate_existing=True))
            if current is None:
                return False
        if isinstance(row, (TradeEvent, MarketEvent)) and (
            getattr(row, "canonical", True) is False or getattr(row, "removed", False) is True
        ):
            return False
        data = metadata(row)
        if (
            data.get("removed")
            or data.get("orphaned")
            or data.get("confirmation_status") == "orphaned"
        ):
            return False
        raw = self.raw_for(row, chain)
        if proof and proof.raw_event_id:
            raw = self.session.get(RawChainEvent, proof.raw_event_id, populate_existing=True)
            if raw is None:
                return False
        if proof and proof.raw_required and raw is None:
            return False
        if (
            isinstance(row, Pair)
            and metadata(row).get("log_index") is None
            and row.creation_tx_hash
        ):
            matches = self.session.scalars(
                select(RawChainEvent).where(
                    RawChainEvent.chain_id == self.chain_id,
                    func.lower(RawChainEvent.transaction_hash) == row.creation_tx_hash.lower(),
                )
            ).all()
            if len(matches) > 1:
                return False  # Ambiguous legacy factory provenance cannot establish validity.
        if raw is not None:
            if raw.removed or raw.confirmation_status == "orphaned":
                return False
            if data.get("block_hash") and data["block_hash"] != raw.block_hash:
                return False
            if proof and proof.block_hash and raw.block_hash != proof.block_hash:
                return False
            expected_hash = getattr(row, "creation_block_hash", None) or getattr(row, "block_hash", None)
            if expected_hash and raw.block_hash != expected_hash:
                return False
        return True

    def meaningful(self, row):
        usd = getattr(row, "value_usd", None) if isinstance(row, TradeEvent) else getattr(row, "usd_value", None)
        if usd is None:
            usd = metadata(row).get("usd_value")
        native = getattr(row, "native_amount", None)
        # MarketEvent.quote_amount may be raw units; never treat it as native/USD.
        if usd is not None:
            return Decimal(str(usd)) >= self.config.min_trade_usd
        return native is not None and Decimal(str(native)) >= self.config.min_trade_native

    def source_kind(self, row):
        if isinstance(row, MarketEvent) and not row.source.startswith("bsc:"):
            return None
        if isinstance(row, Pair):
            return "PAIR_CREATED"
        if isinstance(row, TokenLifecycleEvent):
            if row.launchpad and row.event_type == "CREATED":
                return "LAUNCHPAD_CREATED"
            if row.launchpad and row.event_type in {
                "TRADING",
                "TRADING_STARTED",
                "NEAR_GRADUATION",
                "LAUNCHED",
                "GRADUATED",
                "MIGRATED",
                "MIGRATED_TO_DEX",
            }:
                return "LAUNCHPAD_LIFECYCLE"
        if (
            isinstance(row, TradeEvent)
            and str(row.side) in {"BUY", "TradeSide.BUY"}
            and self.meaningful(row)
        ):
            return "FIRST_TRADE"
        if (
            isinstance(row, MarketEvent)
            and row.event_type in {"SWAP", "MINT", "LIQUIDITY_ADD"}
            and self.meaningful(row)
        ):
            return "MARKET_ACTIVITY"
        return None

    def ingest(self, row, *, at=None):
        at = utc(at or datetime.now(UTC))
        self.session.flush()
        kind = self.source_kind(row)
        if not kind or not observed(row) or utc(observed(row)) > at:
            return []
        chain = "bsc" if isinstance(row, (Pair, MarketEvent)) else row.chain
        if isinstance(row, (Pair, MarketEvent)) and row.chain_id != self.chain_id:
            return []
        if not self.canonical(row, chain):
            return []
        if isinstance(row, Pair):
            addresses = {row.token0_address, row.token1_address} - {row.quote_token}
        else:
            addresses = {row.token_address} - {None}
        results = []
        for address in sorted(addresses):
            token_query = select(Token).where(Token.chain == chain)
            token_query = (
                token_query.where(func.lower(Token.address) == address.lower())
                if chain == "bsc"
                else token_query.where(Token.address == address)
            )
            token = self.session.scalar(token_query)
            if token is None:
                # Candidate creation must never invent a duplicate Token record.
                continue
            candidate = self.session.scalar(
                select(RadarCandidate).where(RadarCandidate.token_id == token.id).with_for_update()
            )
            if candidate is None:
                try:
                    with self.session.begin_nested():
                        candidate = RadarCandidate(
                            token_id=token.id,
                            discovered_at=observed(row),
                            discovery_source=kind,
                            discovery_reason="CANONICAL_CHAIN_EVENT",
                            latest_seen_at=observed(row),
                            created_at=at,
                            updated_at=at,
                        )
                        self.session.add(candidate)
                        self.session.flush()
                        self.session.add(
                            RadarCandidateHistory(
                                candidate_id=candidate.id,
                                new_status=S.DISCOVERED,
                                reason="CANONICAL_CHAIN_EVENT",
                                timestamp=at,
                            )
                        )
                        metrics.record_candidate_discovery()
                except IntegrityError:
                    candidate = self.session.scalar(
                        select(RadarCandidate)
                        .where(RadarCandidate.token_id == token.id)
                        .with_for_update()
                    )
                    if candidate is None:
                        raise
            source, column = next(
                (name, col) for name, (model, col) in SOURCES.items() if isinstance(row, model)
            )
            key = f"{source}:{row.id}"
            proof = self.session.scalar(
                select(RadarCandidateEvidence).where(
                    RadarCandidateEvidence.candidate_id == candidate.id,
                    RadarCandidateEvidence.source_key == key,
                )
            )
            raw = self.raw_for(row, chain)
            if proof is None:
                proof = RadarCandidateEvidence(
                    candidate_id=candidate.id, source_key=key, source_kind=kind, **{column: row.id}
                )
                self.session.add(proof)
            if proof.raw_event_id is None or metadata(row).get("block_hash") == (
                raw.block_hash if raw else None
            ):
                proof.raw_required = raw is not None
                proof.raw_event_id = raw.id if raw else None
                proof.block_hash = raw.block_hash if raw else None
            self.session.flush()
            self.refresh(candidate, at=at)
            results.append(candidate)
        return results

    def move(self, candidate, new, reason, at):
        if candidate.status != new:
            self.session.add(candidate.transition(new, reason=reason, at=at))

    def refresh(self, candidate, *, at=None):
        from .projection import apply_projection, build_projection

        at = utc(at or datetime.now(UTC))
        snapshot = build_projection(self, candidate, at=at)
        apply_projection(self, candidate, snapshot)
        return candidate

    def features(self, token, candidate, at, *, lineage=None):
        start = at - timedelta(minutes=5)
        address = normalized(token.chain, token.address)
        trades = list(
            self.session.scalars(
                select(TradeEvent).where(
                    TradeEvent.chain == token.chain,
                    TradeEvent.token_address == address,
                    TradeEvent.timestamp > start,
                    TradeEvent.timestamp <= at,
                    *canonical_fact_conditions(TradeEvent),
                ).execution_options(populate_existing=True)
            ).all()
        )
        markets = (
            list(
                self.session.scalars(
                    select(MarketEvent).where(
                        MarketEvent.chain_id == self.chain_id,
                        MarketEvent.token_address == address,
                        MarketEvent.timestamp > start,
                        MarketEvent.timestamp <= at,
                        *canonical_fact_conditions(MarketEvent),
                    ).execution_options(populate_existing=True)
                ).all()
            )
            if token.chain == "bsc"
            else []
        )
        # The same tx/log can exist in both canonical projections; count it once.
        activity = {}
        for row in markets + trades:
            if self.canonical(row, token.chain):
                activity[(row.tx_hash.lower(), row.event_index)] = row
        rows = list(activity.values())
        market_snapshot = self.session.scalar(
            select(TokenMarketSnapshot)
            .where(
                TokenMarketSnapshot.chain == token.chain,
                TokenMarketSnapshot.token_address == address,
                TokenMarketSnapshot.as_of <= at,
                TokenMarketSnapshot.observed_at <= at,
            )
            .order_by(TokenMarketSnapshot.as_of.desc(), TokenMarketSnapshot.id.desc())
            .limit(1)
        ) if token.chain == "bsc" else None
        if rows:
            candidate.latest_seen_at = max(
                utc(candidate.latest_seen_at), max(utc(row.timestamp) for row in rows)
            )
        f = RadarFeatureSnapshot.unknown(
            age_seconds=int((at - utc(candidate.discovered_at)).total_seconds()),
            market_phase=candidate.stage,
        ).as_dict()
        reasons = {}
        if market_snapshot is not None:
            for field in ("price_usd", "market_cap_usd", "liquidity_usd", "volume_1m", "volume_5m"):
                target = {"price_usd": "price", "market_cap_usd": "market_cap", "liquidity_usd": "liquidity"}.get(field, field)
                value = getattr(market_snapshot, field, None)
                if value is not None:
                    f[target] = str(value)
                else:
                    reasons[target] = "NO_PRICE" if target == "price" else "NO_MARKET_DATA"
            f["buy_count_1m"] = market_snapshot.buys_1m if market_snapshot.buys_1m is not None else f["buy_count_1m"]
            f["sell_count_1m"] = market_snapshot.sells_1m if market_snapshot.sells_1m is not None else f["sell_count_1m"]
            f["buy_count_5m"] = market_snapshot.buys_5m if market_snapshot.buys_5m is not None else f["buy_count_5m"]
            f["sell_count_5m"] = market_snapshot.sells_5m if market_snapshot.sells_5m is not None else f["sell_count_5m"]
            f["unique_buyers_1m"] = market_snapshot.unique_buyers_1m if market_snapshot.unique_buyers_1m is not None else f["unique_buyers_1m"]
            f["unique_buyers_5m"] = market_snapshot.unique_buyers_5m if market_snapshot.unique_buyers_5m is not None else f["unique_buyers_5m"]
        for minutes in (1, 5):
            window = [
                r
                for r in rows
                if utc(r.timestamp) > at - timedelta(minutes=minutes)
                and str(getattr(r, "side", "")).split(".")[-1] in {"BUY", "SELL"}
            ]
            buys = [r for r in window if str(r.side).split(".")[-1] == "BUY"]
            sells = [r for r in window if str(r.side).split(".")[-1] == "SELL"]
            no_activity = not window and market_snapshot is not None
            f[f"buy_count_{minutes}m"] = len(buys) if window else 0 if no_activity else UNKNOWN
            f[f"sell_count_{minutes}m"] = len(sells) if window else 0 if no_activity else UNKNOWN
            f[f"buy_sell_ratio_{minutes}m"] = (
                str(Decimal(len(buys)) / len(sells)) if sells else UNKNOWN
            )
            for name, subset in (("buyers", buys), ("sellers", sells)):
                wallets = [
                    getattr(r, "trader_address", None) or getattr(r, "wallet_address", None)
                    for r in subset
                ]
                f[f"unique_{name}_{minutes}m"] = (
                    len(set(wallets)) if window and all(wallets) else 0 if no_activity else UNKNOWN
                )
        for field in ("price", "liquidity", "market_cap"):
            observations = [
                (utc(r.timestamp), usd_price(r) if field == "price" else getattr(r, field, None))
                for r in rows
                if (usd_price(r) if field == "price" else getattr(r, field, None)) is not None
            ]
            if observations:
                f[field] = str(max(observations, key=lambda p: p[0])[1])
        lower = at - timedelta(seconds=self.config.snapshot_max_age_seconds)
        dependencies = []
        for model, version_field, version, target in (
            (
                TokenSmartMoneySnapshot,
                "score_version",
                self.config.score_version,
                "smart_snapshot_id",
            ),
            (TokenRiskSnapshot, "risk_version", self.config.risk_version, "risk_snapshot_id"),
        ):
            snap = self.session.scalar(
                select(model)
                .where(
                    model.chain == token.chain,
                    model.token_address == address,
                    model.as_of > lower,
                    model.as_of <= at,
                    getattr(model, version_field) == version,
                )
                .order_by(model.as_of.desc(), model.id.desc())
                .limit(1)
                .execution_options(populate_existing=True)
            )
            setattr(candidate, target, snap.id if snap else None)
            if snap:
                dependencies.append({"kind": snap.__tablename__, "id": snap.id,
                                     "digest": (snap.input_manifest or {}).get("digest"),
                                     "source_epoch": snap.source_epoch})
            if isinstance(snap, TokenSmartMoneySnapshot) and snap.data_status in {"OK", "DEGRADED"}:
                f.update(
                    known_smart_wallet_count=snap.smart_wallet_count,
                    smart_money_net_flow=str(snap.smart_money_net_flow),
                    smart_money_score=str(snap.smart_money_score)
                    if snap.smart_money_score is not None
                    else UNKNOWN,
                )
            if isinstance(snap, TokenRiskSnapshot):
                f["risk"] = snap.overall_risk
        signal = self.session.scalar(
            select(Signal)
            .where(
                Signal.token_id == token.id,
                Signal.created_at > lower,
                Signal.created_at <= at,
                Signal.model_version == self.config.signal_version,
                Signal.data_status == "OK",
            )
            .order_by(Signal.created_at.desc(), Signal.id.desc())
            .limit(1)
        )
        candidate.latest_signal_id = signal.id if signal and is_current_snapshot(self.session, signal) else None
        if token.chain == "bsc":
            early = load_early_entries(self.session, address, as_of=at)
            if early.reason is None:
                f["early_buyer_count"] = len(early.entries)
            else:
                reasons["early_buyer_count"] = early.reason
            if lineage is not None:
                lineage["early_entry"] = {"entries": early.entries, "reason": early.reason,
                                          "source_manifest": early.source_manifest}
        if not rows and market_snapshot is None:
            reasons.update({key: "NO_SWAP_DATA" for key in ("buy_count_1m", "sell_count_1m", "buy_count_5m", "sell_count_5m", "unique_buyers_1m", "unique_buyers_5m")})
        if f.get("smart_money_score") == UNKNOWN:
            reasons["smart_money_score"] = "NO_SMART_MONEY_SAMPLE"
        f["reason_codes"] = reasons
        if lineage is not None:
            lineage.update(facts={"activity": [{"table": row.__tablename__, "values": row_values(row)} for row in rows]},
                           dependencies=dependencies)
            lineage["facts"]["early_entry"] = lineage.pop("early_entry", {})
            if market_snapshot is not None:
                lineage["dependencies"].append({"kind": market_snapshot.__tablename__, "id": market_snapshot.id,
                                                 "digest": (market_snapshot.input_manifest or {}).get("digest"),
                                                 "source_epoch": market_snapshot.source_epoch})
        return RadarFeatureSnapshot.from_mapping(f).as_dict()

    def recover(self, *, at=None):
        """Bounded catch-up of normalized facts plus revalidation of persisted candidates."""
        at = utc(at or datetime.now(UTC))
        start = at - timedelta(seconds=self.config.discovery_lookback_seconds)
        for model, column in (
            (Pair, Pair.creation_timestamp),
            (TokenLifecycleEvent, TokenLifecycleEvent.timestamp),
            (TradeEvent, TradeEvent.timestamp),
            (MarketEvent, MarketEvent.timestamp),
        ):
            for row in self.session.scalars(
                select(model).where(column >= start, column <= at).order_by(column, model.id)
            ):
                self.ingest(row, at=at)
        for candidate in self.session.scalars(select(RadarCandidate).order_by(RadarCandidate.id)):
            self.refresh(candidate, at=at)

    def revalidate_raw(self, raw, *, at=None):
        at = utc(at or datetime.now(UTC))
        ids = set(
            self.session.scalars(
                select(RadarCandidateEvidence.candidate_id).where(
                    RadarCandidateEvidence.raw_event_id == raw.id
                )
            ).all()
        )
        # A removed sell can invalidate fast features and derived snapshots even if
        # it never qualified as discovery evidence. Also handles raw evidence arriving late.
        for model in (TradeEvent, MarketEvent, TokenLifecycleEvent):
            chain_filter = (
                model.chain_id == raw.chain_id if model is MarketEvent else model.chain == "bsc"
            )
            addresses = self.session.scalars(
                select(model.token_address).where(
                    chain_filter,
                    func.lower(model.tx_hash) == raw.transaction_hash.lower(),
                    model.event_index == raw.log_index,
                )
            ).all()
            ids.update(
                self.session.scalars(
                    select(RadarCandidate.id)
                    .join(Token, Token.id == RadarCandidate.token_id)
                    .where(Token.chain == "bsc", Token.address.in_(addresses))
                ).all()
            )
        for candidate_id in sorted(ids):
            candidate = self.session.get(RadarCandidate, candidate_id)
            if raw.removed or raw.confirmation_status == "orphaned":
                candidate.invalidated_at = at
            self.refresh(candidate, at=at)

    def refresh_for_event(self, row, *, at=None):
        """Update existing candidates even when this event is not a discovery trigger."""
        if isinstance(row, Pair):
            addresses, chain = (row.token0_address, row.token1_address), "bsc"
        else:
            addresses = (row.token_address,)
            chain = "bsc" if isinstance(row, MarketEvent) else row.chain
        refreshed = []
        for candidate in self.session.scalars(
            select(RadarCandidate)
            .join(Token, Token.id == RadarCandidate.token_id)
            .where(Token.chain == chain, Token.address.in_(addresses))
        ):
            self.refresh(candidate, at=at)
            refreshed.append(candidate)
        return refreshed

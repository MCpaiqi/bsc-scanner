"""Materialize reusable point-in-time wallet and token intelligence snapshots."""

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from types import SimpleNamespace
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from smms.chains.constants import normalize_address, validate_bsc_chain
from smms.db.facts import canonical_fact_conditions
from smms.db.models import (
    Pair,
    Token,
    TokenSmartMoneySnapshot,
    TradeEvent,
    WalletSmartMoneySnapshot,
)
from smms.db.repositories import (
    TokenSmartMoneySnapshotRepository,
    WalletSmartMoneySnapshotRepository,
)
from smms.research.lineage import digest, manifest, row_values
from smms.research.state import capture_epoch

from .early_buyers import EarlyBuyerConfig, identify_early_buyers
from .profile import WalletProfile, WalletProfileReplay
from .replay import usd_price
from .scoring import SmartMoneyMetrics, aggregate_token_smart_money, calculate_smart_money_score

SCORE_VERSION = "v2"
PROFILE_VERSION = "v1"
WALLET_INPUT_POLICY = "canonical-wallet-usd-v2"
TOKEN_INPUT_POLICY = "canonical-token-usd-v2"


class StaleAggregationInputs(ValueError):
    """Replay or publication lost its exact source generation; retry the read."""


@dataclass(frozen=True)
class AggregationResult:
    as_of: datetime
    score_version: str
    profile_version: str
    wallet_snapshots: int
    token_snapshots: int


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _trade_payload(row: TradeEvent) -> dict[str, Any]:
    return {
        "wallet_address": row.trader_address,
        "token_address": row.token_address,
        "side": row.side.value if hasattr(row.side, "value") else row.side,
        "timestamp": row.timestamp,
        "tx_hash": row.tx_hash,
        "event_index": row.event_index,
        "block_number": row.block_number,
        "token_amount": row.token_amount,
        "native_amount": None,
        # Explicit valuation only. Cached outcome/creation annotations cannot
        # override canonical Pair evidence or supply future performance labels.
        "usd_value": row.value_usd,
        "price": usd_price(row),
        "price_basis": "USD",
        "market_cap": None,
        "liquidity": None,
        "metadata_json": {},
    }


def _score_profile(profile: WalletProfile):
    completeness = Decimal("0.8") * Decimal(profile.valued_positions) / Decimal(profile.sample_size) if profile.sample_size else Decimal(0)
    coverage = min(Decimal(1), Decimal(profile.sample_size) / Decimal(12))
    quality = max(
        Decimal(0),
        Decimal(1) - (profile.bot_probability + profile.wash_probability) / Decimal(2),
    )
    inputs = SmartMoneyMetrics(
            win_rate=profile.win_rate,
            avg_roi=profile.roi,
            median_roi=profile.median_roi,
            risk_adjusted_return=profile.roi / (Decimal(1) + profile.max_drawdown),
            early_entry_rate=profile.early_entry_rate,
            high_multiple_rate=(
                Decimal(profile.threshold_hits.get("5x+", 0)) / Decimal(profile.sample_size)
                if profile.sample_size
                else Decimal(0)
            ),
            consistency_rate=profile.consistency,
            rug_exposure=profile.rug_exposure,
            bot_probability=profile.bot_probability,
            wash_trading_probability=profile.wash_probability,
            sample_size=profile.sample_size,
            data_completeness=completeness,
            historical_coverage=coverage,
            trade_quality=quality,
        )
    result = calculate_smart_money_score(inputs)
    return result, completeness, coverage, quality, inputs


class IntelligenceAggregationService:
    """Materialize full or token-scoped point-in-time intelligence."""

    def __init__(
        self,
        session: Session,
        *,
        chain: str = "bsc",
        score_version: str = SCORE_VERSION,
        profile_version: str = PROFILE_VERSION,
        min_wallet_score: Decimal | int = Decimal(60),
        early_buyer_config: EarlyBuyerConfig | None = None,
        reconstruction_policy: str | None = None,
    ) -> None:
        self.session = session
        self.chain = validate_bsc_chain(chain)
        self.score_version = score_version
        self.profile_version = profile_version
        self.min_wallet_score = Decimal(min_wallet_score)
        self.early_buyer_config = early_buyer_config or EarlyBuyerConfig()
        self.reconstruction_policy = reconstruction_policy

    def _check_epoch(self, epoch):
        if capture_epoch(self.session, self.chain) != epoch:
            raise StaleAggregationInputs("canonical input epoch changed during aggregation")

    def refresh(self, *, as_of: datetime | None = None) -> AggregationResult:
        cutoff = self._cutoff(as_of)
        source_epoch = capture_epoch(self.session, self.chain)
        trades = list(
            self.session.scalars(
                select(TradeEvent)
                .where(
                    TradeEvent.chain == self.chain,
                    TradeEvent.timestamp <= cutoff,
                    *canonical_fact_conditions(TradeEvent),
                )
                .order_by(TradeEvent.timestamp, TradeEvent.id).execution_options(populate_existing=True)
            ).all()
        )
        token_addresses = {row.token_address for row in trades}
        tokens = list(
            self.session.scalars(
                select(Token).where(Token.chain == self.chain, Token.address.in_(token_addresses)).execution_options(populate_existing=True)
            ).all()
        ) if token_addresses else []
        pairs = list(
            self.session.scalars(
                select(Pair).where(
                    Pair.chain_id == 56,
                    Pair.creation_timestamp.is_not(None),
                    Pair.creation_timestamp <= cutoff,
                    *canonical_fact_conditions(Pair),
                    or_(Pair.token0_address.in_(token_addresses), Pair.token1_address.in_(token_addresses)),
                ).execution_options(populate_existing=True)
            ).all()
        ) if token_addresses else []
        created_at = self._created_at(pairs, tokens)

        by_wallet: dict[str, list[dict[str, Any]]] = defaultdict(list)
        by_token: dict[str, list[dict[str, Any]]] = defaultdict(list)
        # Historical subjects remain addressable when their last valid fact is
        # removed. Their empty replacement carries UNKNOWN, never the old score.
        subjects = self.session.execute(select(TradeEvent.trader_address, TradeEvent.token_address).where(
            TradeEvent.chain == self.chain, TradeEvent.timestamp <= cutoff,
        ).distinct()).all()
        for wallet, address in subjects:
            by_wallet[normalize_address(wallet, chain=self.chain)]
            by_token[normalize_address(address, chain=self.chain)]
        for row in trades:
            payload = _trade_payload(row)
            by_wallet[normalize_address(row.trader_address, chain=self.chain)].append(payload)
            by_token[normalize_address(row.token_address, chain=self.chain)].append(payload)

        return self._materialize(
            cutoff=cutoff,
            by_wallet=by_wallet,
            by_token=by_token,
            created_at=created_at, source_epoch=source_epoch, source_trades=trades, source_pairs=pairs,
        )

    def refresh_token(
        self, token_address: str, *, as_of: datetime | None = None
    ) -> AggregationResult:
        """Refresh one token and only wallets affected by that token's trades.

        Wallet profiles still use each affected wallet's chain history, preserving
        score semantics without scanning unrelated wallets or tokens.
        """

        cutoff = self._cutoff(as_of)
        source_epoch = capture_epoch(self.session, self.chain)
        address = normalize_address(token_address, chain=self.chain)
        token_trades = list(
            self.session.scalars(
                select(TradeEvent)
                .where(
                    TradeEvent.chain == self.chain,
                    TradeEvent.token_address == address,
                    TradeEvent.timestamp <= cutoff,
                    *canonical_fact_conditions(TradeEvent),
                )
                .order_by(TradeEvent.timestamp, TradeEvent.id).execution_options(populate_existing=True)
            ).all()
        )
        affected_wallets = {normalize_address(row.trader_address, chain=self.chain) for row in token_trades}
        wallet_trades = (
            list(
                self.session.scalars(
                    select(TradeEvent)
                    .where(
                        TradeEvent.chain == self.chain,
                    TradeEvent.trader_address.in_(affected_wallets),
                    TradeEvent.timestamp <= cutoff,
                    *canonical_fact_conditions(TradeEvent),
                    )
                    .order_by(TradeEvent.timestamp, TradeEvent.id).execution_options(populate_existing=True)
                ).all()
            )
            if affected_wallets
            else []
        )
        observed_tokens = {row.token_address for row in wallet_trades} | {address}
        tokens = list(
            self.session.scalars(
                select(Token).where(Token.chain == self.chain, Token.address.in_(observed_tokens)).execution_options(populate_existing=True)
            ).all()
        )
        pairs = list(
            self.session.scalars(
                select(Pair).where(
                    Pair.chain_id == 56,
                    Pair.creation_timestamp.is_not(None),
                    Pair.creation_timestamp <= cutoff,
                    *canonical_fact_conditions(Pair),
                    or_(Pair.token0_address.in_(observed_tokens), Pair.token1_address.in_(observed_tokens)),
                ).execution_options(populate_existing=True)
            ).all()
        )
        by_wallet: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in wallet_trades:
            by_wallet[normalize_address(row.trader_address, chain=self.chain)].append(_trade_payload(row))
        return self._materialize(
            cutoff=cutoff,
            by_wallet=by_wallet,
            by_token={address: [_trade_payload(row) for row in token_trades]},
            created_at=self._created_at(pairs, tokens), source_epoch=source_epoch,
            source_trades=list({row.id: row for row in [*token_trades, *wallet_trades]}.values()), source_pairs=pairs,
        )

    def refresh_wallet(self, wallet_address: str, *, as_of: datetime | None = None) -> AggregationResult:
        cutoff = self._cutoff(as_of)
        source_epoch = capture_epoch(self.session, self.chain)
        address = normalize_address(wallet_address, chain=self.chain)
        trades = list(self.session.scalars(select(TradeEvent).where(
            TradeEvent.chain == self.chain, TradeEvent.trader_address == address,
            TradeEvent.timestamp <= cutoff, *canonical_fact_conditions(TradeEvent),
        ).order_by(TradeEvent.timestamp, TradeEvent.id).execution_options(populate_existing=True)))
        tokens = {row.token_address for row in trades}
        pairs = list(self.session.scalars(select(Pair).where(
            Pair.chain_id == 56, Pair.creation_timestamp <= cutoff, *canonical_fact_conditions(Pair),
            or_(Pair.token0_address.in_(tokens), Pair.token1_address.in_(tokens)),
        ).execution_options(populate_existing=True))) if tokens else []
        return self._materialize(cutoff=cutoff, by_wallet={address: [_trade_payload(row) for row in trades]},
                                 by_token={}, created_at=self._created_at(pairs), source_epoch=source_epoch,
                                 source_trades=trades, source_pairs=pairs)

    @staticmethod
    def _cutoff(value: datetime | None) -> datetime:
        cutoff = value or datetime.now(UTC)
        return cutoff.replace(tzinfo=UTC) if cutoff.tzinfo is None else cutoff.astimezone(UTC)

    @staticmethod
    def _created_at(pairs: list[Pair], tokens: list[Token] | None = None) -> dict[str, datetime | None]:
        """Return the verified PairCreated timestamp for each token.

        Token ingestion timestamps and provider first-seen times are not valid
        T0 values for early-buyer attribution, so tokens without a canonical
        pair remain intentionally absent from this map.
        """
        created: dict[str, datetime] = {}
        for row in pairs:
            if row.creation_timestamp is None:
                continue
            stamp = row.creation_timestamp
            for address in (row.token0_address, row.token1_address):
                key = normalize_address(address, chain="bsc")
                if key not in created or stamp < created[key]:
                    created[key] = stamp
        return created

    def _materialize(
        self,
        *,
        cutoff: datetime,
        by_wallet: dict[str, list[dict[str, Any]]],
        by_token: dict[str, list[dict[str, Any]]],
        created_at: dict[str, datetime | None],
        source_epoch: int,
        source_trades: list[TradeEvent],
        source_pairs: list[Pair],
    ) -> AggregationResult:
        wallet_repository = WalletSmartMoneySnapshotRepository(self.session)
        self._check_epoch(source_epoch)
        profiles = {}
        confidence_scores: dict[str, Decimal] = {}
        wallet_sources = {}
        parameters = {"as_of": cutoff, "score_version": self.score_version,
                      "profile_version": self.profile_version, "min_wallet_score": self.min_wallet_score,
                      "early_buyer_config": asdict(self.early_buyer_config),
                      "reconstruction_policy": self.reconstruction_policy,
                      "input_policy": TOKEN_INPUT_POLICY}
        for wallet, wallet_trades in by_wallet.items():
            profile = WalletProfileReplay(chain=self.chain).replay(
                wallet,
                trades=wallet_trades,
                token_metadata={
                    address: {"created_at": timestamp}
                    for address, timestamp in created_at.items()
                    if timestamp is not None
                },
                as_of=cutoff,
            )
            score, completeness, coverage, quality, score_inputs = _score_profile(profile)
            wallet_rows = [row for row in source_trades if row.trader_address == wallet]
            related = {row.token_address for row in wallet_rows}
            relevant_pairs = [row for row in source_pairs if related & {row.token0_address, row.token1_address}]
            computed = WalletSmartMoneySnapshot(
                    source_epoch=source_epoch, input_manifest=manifest(
                        # Token admission/window settings do not change this
                        # wallet replay. Including them causes policy-only
                        # revisions to invalidate unrelated token dependents.
                        kind="wallet_smart_money_snapshots", parameters={
                            "as_of": cutoff, "score_version": self.score_version,
                            "profile_version": self.profile_version, "wallet_address": wallet,
                            "input_policy": WALLET_INPUT_POLICY,
                        },
                        facts={"trades": [row_values(row) for row in wallet_rows],
                               "pairs": [row_values(row) for row in relevant_pairs],
                               "score_inputs": asdict(score_inputs), "normalized_trades": wallet_trades},
                    ),
                    chain=self.chain,
                    wallet_address=wallet,
                    score=score.score,
                    confidence=score.confidence,
                    confidence_score=score.confidence_score,
                    score_version=self.score_version,
                    profile_version=self.profile_version,
                    as_of=cutoff,
                    sample_size=profile.sample_size,
                    data_completeness=completeness,
                    historical_coverage=coverage,
                    trade_quality=quality,
                    profile_json=_json_value(profile.to_dict()),
                    components_json=_json_value(score.components),
                    contributions_json=_json_value(score.contributions),
                )
            self._check_epoch(source_epoch)
            wallet_record = wallet_repository.upsert(computed)
            if (wallet_record.source_epoch != source_epoch
                    or wallet_record.input_manifest.get("digest") != computed.input_manifest.get("digest")
                    or digest(wallet_record.profile_json) != digest(computed.profile_json)
                    or wallet_record.score != computed.score or wallet_record.snapshot_status != "VALID"):

                raise StaleAggregationInputs("publication winner differs from computed wallet inputs")
            profiles[wallet] = SimpleNamespace(smart_money_score=wallet_record.score,
                behavioral_indicators=wallet_record.profile_json.get("behavioral_indicators", {}))
            confidence_scores[wallet] = wallet_record.confidence_score

            wallet_sources[wallet] = {"kind": "wallet_smart_money_snapshots", "id": wallet_record.id,
                                      "digest": wallet_record.input_manifest["digest"], "source_epoch": wallet_record.source_epoch}

        token_repository = TokenSmartMoneySnapshotRepository(self.session)
        for token_address, token_trades in by_token.items():
            self._check_epoch(source_epoch)
            token_created_at = created_at.get(token_address)
            early = (
                identify_early_buyers(
                    token_address,
                    token_trades,
                    token_created_at=token_created_at,
                    config=self.early_buyer_config,
                )
                if token_created_at is not None
                else []
            )
            summary = aggregate_token_smart_money(
                token_address,
                profiles,
                token_trades,
                early_buyers=early,
                min_wallet_score=self.min_wallet_score,
            )
            selected_confidence = [
                confidence_scores[wallet]
                for wallet in summary.top_smart_wallets
                if wallet in confidence_scores
            ]
            confidence_score = (
                sum(selected_confidence, Decimal(0)) / Decimal(len(selected_confidence))
                if selected_confidence
                else Decimal(0)
            )
            confidence = (
                "HIGH" if confidence_score >= 70 else "MEDIUM" if confidence_score >= 40 else "LOW"
            )
            token_repository.upsert(
                TokenSmartMoneySnapshot(
                    source_epoch=source_epoch, input_manifest=manifest(
                        kind="token_smart_money_snapshots", parameters={**parameters, "token_address": token_address},
                        facts={"trades": [row_values(row) for row in source_trades if row.token_address == token_address],
                               "pairs": [row_values(row) for row in source_pairs if token_address in {row.token0_address, row.token1_address}]},
                        dependencies=[wallet_sources[wallet] for wallet in sorted({trade["wallet_address"] for trade in token_trades}) if wallet in wallet_sources],
                    ),
                    chain=self.chain,
                    token_address=token_address,
                    smart_wallet_count=summary.smart_wallet_count,
                    smart_money_buy_usd=summary.smart_money_buy_usd,
                    smart_money_sell_usd=summary.smart_money_sell_usd,
                    smart_money_net_flow=summary.smart_money_net_flow,
                    early_smart_buy_count=summary.early_smart_buy_count,
                    smart_money_score=summary.smart_money_score,
                    confidence=confidence,
                    confidence_score=confidence_score,
                    data_status=summary.data_status,
                    as_of=cutoff,
                    score_version=self.score_version,
                    top_smart_wallets_json=list(summary.top_smart_wallets),
                )
            )

        self._check_epoch(source_epoch)
        return AggregationResult(
            as_of=cutoff,
            score_version=self.score_version,
            profile_version=self.profile_version,
            wallet_snapshots=len(by_wallet),
            token_snapshots=len(by_token),
        )


__all__ = [
    "PROFILE_VERSION",
    "SCORE_VERSION",
    "AggregationResult",
    "IntelligenceAggregationService",
]

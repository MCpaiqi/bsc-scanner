import subprocess
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from smms.db.models import (
    Base,
    Token,
    TokenSmartMoneySnapshot,
    TradeEvent,
    TradeSide,
    WalletPerformance,
    WalletSmartMoneySnapshot,
)
from smms.db.repositories import (
    TokenSmartMoneySnapshotRepository,
    WalletSmartMoneySnapshotRepository,
)
from smms.smart_money import SmartMoneyMetrics, build_wallet_profile, calculate_smart_money_score
from smms.smart_money.aggregation import IntelligenceAggregationService


def test_ingestion_package_import_does_not_load_realtime_or_database_modules() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import smms.ingestion; "
                "assert 'smms.ingestion.bsc_stream' not in sys.modules; "
                "assert 'smms.db.database' not in sys.modules; "
                "assert 'smms.db.redis' not in sys.modules"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_roi_normalization_is_monotonic_bounded_and_resists_outliers() -> None:
    values = [Decimal("0.5"), Decimal(1), Decimal(5), Decimal(20), Decimal(100)]
    for metric, component in (("avg_roi", "roi"), ("median_roi", "median_roi")):
        contributions = [
            calculate_smart_money_score(
                SmartMoneyMetrics(**{metric: value})
            ).components[component]
            for value in values
        ]

        assert contributions == sorted(contributions)
        assert len(set(contributions)) == len(contributions)
        assert all(Decimal(0) <= value < Decimal(1) for value in contributions)
        assert contributions[-1] - contributions[-2] < contributions[1] - contributions[0]

    thousand_x = calculate_smart_money_score(
        SmartMoneyMetrics(avg_roi=Decimal(1000))
    ).contributions["roi"]
    assert thousand_x <= Decimal("14.00")


def test_same_block_activity_is_correlation_only_not_bot_or_wash_evidence() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)

    def trades(blocks: list[int]) -> list[dict[str, object]]:
        return [
            {
                "wallet_address": "wallet",
                "token_address": f"token-{index}",
                "side": "BUY",
                "timestamp": start + timedelta(seconds=index),
                "tx_hash": f"tx-{index}",
                "token_amount": Decimal(1),
                "native_amount": Decimal(index + 1),
                "price": Decimal(index + 1),
                "block_number": block,
            }
            for index, block in enumerate(blocks)
        ]

    spread = build_wallet_profile("wallet", trades=trades([1, 2, 3, 4]))
    correlated = build_wallet_profile("wallet", trades=trades([7, 7, 7, 7]))

    assert correlated.behavioral_indicators["same_block_rate"] == Decimal(1)
    assert correlated.behavioral_indicators["correlation_indicators"]["same_block_rate"] == Decimal(1)
    assert correlated.bot_probability == spread.bot_probability
    assert correlated.wash_probability == spread.wash_probability


def test_versioned_snapshots_coexist_and_historical_reads_ignore_future_rows() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    past = datetime(2026, 1, 1, tzinfo=UTC)
    future = past + timedelta(days=1)

    with Session(engine) as session:
        wallets = WalletSmartMoneySnapshotRepository(session)
        tokens = TokenSmartMoneySnapshotRepository(session)
        for version, score in (("v1", "41"), ("v2", "42")):
            wallets.upsert(
                WalletSmartMoneySnapshot(
                    chain="bsc",
                    wallet_address="Wallet",
                    score=Decimal(score),
                    confidence="LOW",
                    confidence_score=Decimal(10),
                    score_version=version,
                    profile_version="v1",
                    as_of=past,
                    sample_size=1,
                    data_completeness=Decimal(1),
                    historical_coverage=Decimal(1),
                    trade_quality=Decimal(1),
                    profile_json={},
                    components_json={},
                    contributions_json={},
                )
            )
        wallets.upsert(
            WalletSmartMoneySnapshot(
                chain="bsc",
                wallet_address="Wallet",
                score=Decimal(99),
                confidence="HIGH",
                confidence_score=Decimal(99),
                score_version="v2",
                profile_version="v1",
                as_of=future,
                sample_size=100,
                data_completeness=Decimal(1),
                historical_coverage=Decimal(1),
                trade_quality=Decimal(1),
                profile_json={},
                components_json={},
                contributions_json={},
            )
        )
        tokens.upsert(
            TokenSmartMoneySnapshot(
                chain="bsc",
                token_address="Token",
                smart_wallet_count=1,
                smart_money_buy_usd=Decimal(10),
                smart_money_sell_usd=Decimal(0),
                smart_money_net_flow=Decimal(10),
                early_smart_buy_count=1,
                smart_money_score=Decimal(42),
                confidence="LOW",
                confidence_score=Decimal(10),
                data_status="CHAIN_EVENTS",
                as_of=past,
                score_version="v2",
                top_smart_wallets_json=["wallet"],
            )
        )
        tokens.upsert(
            TokenSmartMoneySnapshot(
                chain="bsc",
                token_address="Token",
                smart_wallet_count=10,
                smart_money_buy_usd=Decimal(1000),
                smart_money_sell_usd=Decimal(0),
                smart_money_net_flow=Decimal(1000),
                early_smart_buy_count=10,
                smart_money_score=Decimal(99),
                confidence="HIGH",
                confidence_score=Decimal(99),
                data_status="CHAIN_EVENTS",
                as_of=future,
                score_version="v2",
                top_smart_wallets_json=["future-wallet"],
            )
        )
        session.flush()

        assert session.query(WalletSmartMoneySnapshot).count() == 3
        assert wallets.get_latest("WALLET", chain="bsc", score_version="v2", as_of=past).score == 42
        assert tokens.get_latest("TOKEN", chain="bsc", score_version="v2", as_of=past).smart_money_buy_usd == 10


def test_aggregation_is_idempotent_and_excludes_all_future_inputs() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    cutoff = datetime(2026, 1, 2, tzinfo=UTC)
    past = cutoff - timedelta(hours=1)
    future = cutoff + timedelta(hours=1)

    with Session(engine) as session:
        session.add(Token(address="token", chain="bsc", creation_timestamp=past))
        session.add_all(
            [
                TradeEvent(
                    chain="bsc",
                    token_address="token",
                    trader_address="wallet",
                    tx_hash="past",
                    event_index=0,
                    block_number=1,
                    timestamp=past,
                    side=TradeSide.BUY,
                    token_amount=Decimal(1),
                    native_amount=Decimal(10),
                    price=Decimal(10),
                    source="test",
                    metadata_json={"usd_value": "10"},
                ),
                TradeEvent(
                    chain="bsc",
                    token_address="token",
                    trader_address="wallet",
                    tx_hash="future",
                    event_index=0,
                    block_number=2,
                    timestamp=future,
                    side=TradeSide.BUY,
                    token_amount=Decimal(1),
                    native_amount=Decimal(1000),
                    price=Decimal(1000),
                    source="test",
                    metadata_json={"usd_value": "1000"},
                ),
                WalletPerformance(
                    chain="bsc",
                    wallet_address="wallet",
                    as_of=future,
                    tokens_traded=100,
                    smart_money_score=Decimal(99),
                ),
            ]
        )
        session.commit()

        service = IntelligenceAggregationService(session, chain="bsc", min_wallet_score=0)
        first = service.refresh(as_of=cutoff)
        second = service.refresh(as_of=cutoff)
        session.commit()

        wallet_rows = session.scalars(select(WalletSmartMoneySnapshot)).all()
        token_rows = session.scalars(select(TokenSmartMoneySnapshot)).all()
        assert first.wallet_snapshots == second.wallet_snapshots == 1
        assert first.token_snapshots == second.token_snapshots == 1
        assert len(wallet_rows) == len(token_rows) == 1
        assert wallet_rows[0].sample_size == 1
        assert token_rows[0].smart_money_buy_usd == Decimal(10)
        assert token_rows[0].early_smart_buy_count == 1


def test_future_token_metadata_and_performance_do_not_contaminate_cutoff() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    cutoff = datetime(2026, 2, 1, tzinfo=UTC)
    past = cutoff - timedelta(hours=1)
    future = cutoff + timedelta(hours=1)

    with Session(engine) as session:
        session.add(Token(address="token", chain="bsc", first_seen_at=future))
        session.add(
            TradeEvent(
                chain="bsc",
                token_address="token",
                trader_address="wallet",
                tx_hash="past",
                event_index=0,
                timestamp=past,
                side=TradeSide.BUY,
                token_amount=Decimal(1),
                native_amount=Decimal(10),
                source="test",
                metadata_json={"usd_value": "10"},
            )
        )
        session.commit()

        IntelligenceAggregationService(session, chain="bsc", min_wallet_score=0).refresh(
            as_of=cutoff
        )
        token_snapshot = session.scalar(select(TokenSmartMoneySnapshot))
        assert token_snapshot.early_smart_buy_count == 0

    profile = build_wallet_profile(
        "wallet",
        performances=[
            {
                "wallet_address": "wallet",
                "token_address": "past",
                "entry_time": past - timedelta(hours=1),
                "exit_time": past,
                "roi": Decimal(1),
                "multiple": Decimal(2),
            },
            {
                "wallet_address": "wallet",
                "token_address": "future",
                "entry_time": past,
                "exit_time": future,
                "roi": Decimal(100),
                "multiple": Decimal(101),
            },
        ],
        as_of=cutoff,
    )
    assert profile.sample_size == 1
    assert profile.roi == 0

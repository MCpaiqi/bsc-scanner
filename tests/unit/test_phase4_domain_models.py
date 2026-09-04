from datetime import UTC, datetime
from decimal import Decimal

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import StatementError
from sqlalchemy.orm import Session

from smms.config import get_settings
from smms.db.models import (
    Base,
    Token,
    TokenLifecycleEvent,
    TradeEvent,
)
from smms.smart_money import (
    EarlyBuyerConfig,
    SmartMoneyMetrics,
    SmartMoneyScoreWeights,
    calculate_smart_money_score,
    identify_early_buyers,
    load_smart_money_weights,
)


def test_phase4_domain_tables_and_indexes_are_present() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    tables = set(inspect(engine).get_table_names())
    assert {
        "trade_events",
        "token_lifecycle_events",
        "wallet_token_performance",
        "wallet_performance",
        "smart_money_scores",
    } <= tables
    indexes = {
        index["name"]
        for table in ("trade_events", "token_lifecycle_events")
        for index in inspect(engine).get_indexes(table)
    }
    assert "ix_trade_events_token_time" in indexes
    assert "ix_trade_events_wallet_time" in indexes
    assert "ix_token_lifecycle_events_token_time" in indexes


def test_phase4_alembic_revisions_upgrade_and_downgrade(tmp_path, monkeypatch) -> None:
    database_path = (tmp_path / "migration.db").as_posix()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path}")
    get_settings.cache_clear()
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{database_path}")
    tables = set(inspect(engine).get_table_names())
    assert {"trade_events", "wallet_performance", "smart_money_scores"} <= tables
    token_columns = {column["name"] for column in inspect(engine).get_columns("tokens")}
    assert {"launchpad", "volume", "holders"} <= token_columns
    assert any(
        "side IN ('BUY', 'SELL')" in constraint["sqltext"]
        for constraint in inspect(engine).get_check_constraints("trade_events")
    )
    command.downgrade(config, "base")
    assert set(inspect(engine).get_table_names()) == {"alembic_version"}
    get_settings.cache_clear()


def test_token_and_trade_event_capture_chain_launchpad_lifecycle_context() -> None:
    token = Token(
        address="0xToken",
        chain="bsc",
        launchpad="FLAP",
        creator_address="0xCreator",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        first_trade_at=datetime(2026, 1, 1, 0, 2, tzinfo=UTC),
        status="TRADING",
    )
    event = TradeEvent(
        chain="bsc",
        launchpad="FLAP",
        token_address="0xToken",
        trader_address="0xWallet",
        tx_hash="0xTx",
        block_number=123,
        timestamp=datetime(2026, 1, 1, 0, 2, tzinfo=UTC),
        side="BUY",
        token_amount=Decimal(10),
        native_amount=Decimal("0.1"),
        event_index=0,
        source="flap.rpc",
    )
    lifecycle = TokenLifecycleEvent(
        chain="bsc",
        launchpad="FLAP",
        token_address="0xToken",
        event_type="CREATED",
        timestamp=token.created_at,
        tx_hash="0xCreate",
        block_number=120,
    )
    assert token.launchpad == "FLAP"
    assert event.side == "BUY"
    assert lifecycle.event_type == "CREATED"


def test_trade_event_rejects_non_canonical_side() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            TradeEvent(
                chain="bsc",
                token_address="0xToken",
                trader_address="0xWallet",
                tx_hash="0xTx",
                timestamp=datetime(2026, 1, 1, tzinfo=UTC),
                side="TRANSFER",
                source="fixture",
            )
        )
        with pytest.raises((StatementError, ValueError)):
            session.commit()


def test_smart_money_score_is_weighted_explainable_and_penalizes_risk() -> None:
    result = calculate_smart_money_score(
        SmartMoneyMetrics(
            win_rate=Decimal("0.8"),
            avg_roi=Decimal(2),
            median_roi=Decimal(1),
            risk_adjusted_return=Decimal("0.9"),
            early_entry_rate=Decimal("0.7"),
            high_multiple_rate=Decimal("0.5"),
            ath_capture_rate=Decimal("0.6"),
            consistency_rate=Decimal("0.75"),
            graduation_rate=Decimal("0.4"),
            rug_exposure=Decimal("0.1"),
            bot_probability=Decimal("0.05"),
            wash_trading_probability=Decimal("0.02"),
        )
    )
    assert Decimal(0) <= result.score <= Decimal(100)
    assert result.penalties > 0
    assert "win_rate" in result.components
    assert result.components["win_rate"] == Decimal("0.8")
    assert result.components["median_roi"] == Decimal(1)
    assert result.components["risk_adjusted_return"] == Decimal("0.9")
    assert result.explanation


def test_score_weights_can_be_loaded_from_a_nested_config_mapping() -> None:
    weights = SmartMoneyScoreWeights.from_mapping(
        {"smart_money": {"win_rate": "0.5", "median_roi": "0.25"}}
    )
    assert weights.win_rate == Decimal("0.5")
    assert weights.median_roi == Decimal("0.25")


def test_checked_in_scoring_yaml_is_loadable() -> None:
    weights = load_smart_money_weights("configs/scoring.yaml")
    assert weights.win_rate == Decimal("0.18")
    assert weights.risk_adjusted_return == Decimal("0.04")


def test_score_calculator_can_use_a_config_file(tmp_path) -> None:
    config = tmp_path / "scoring.yaml"
    config.write_text(
        "smart_money:\n  win_rate: 1\n  roi: 0\n  median_roi: 0\n"
        "  early_entry: 0\n  high_multiple: 0\n  ath_capture: 0\n"
        "  consistency: 0\n  graduation: 0\n  risk_adjusted_return: 0\n",
        encoding="utf-8",
    )
    result = calculate_smart_money_score(
        SmartMoneyMetrics(win_rate=Decimal(1)), config_path=config
    )
    assert result.score == Decimal(100)


def test_early_buyer_exposes_point_in_time_entry_fields_without_future_outcomes() -> None:
    created = datetime(2026, 1, 1, tzinfo=UTC)
    buyers = identify_early_buyers(
        "0xToken",
        [
            {
                "wallet_address": "0xWallet",
                "signature": "0xTx",
                "timestamp": datetime(2026, 1, 1, 0, 3, tzinfo=UTC),
                "side": "BUY",
                "token_out": "0xToken",
                "usd_value": Decimal(2),
                "amount_out": Decimal(100),
                "price": Decimal("0.02"),
            }
        ],
        token_created_at=created,
        config=EarlyBuyerConfig(top_n=5, max_entry_hours=24),
    )
    buyer = buyers[0]
    assert buyer.token_address == "0xToken"
    assert buyer.token_age_at_buy_seconds == 180
    assert buyer.first_buy_amount == Decimal(100)
    assert buyer.buy_count == 1
    assert buyer.buy_rank == 1
    assert buyer.pnl_at_ath is None


def test_early_buyer_summarizes_observed_sells_pnl_and_lifecycle_flags() -> None:
    created = datetime(2026, 1, 1, tzinfo=UTC)
    buyers = identify_early_buyers(
        "0xToken",
        [
            {
                "wallet_address": "0xWallet",
                "signature": "0xBuy",
                "timestamp": created,
                "side": "BUY",
                "token_out": "0xToken",
                "amount_out": Decimal(100),
                "usd_value": Decimal(10),
                "price": Decimal("0.10"),
            },
            {
                "wallet_address": "0xWallet",
                "signature": "0xSell",
                "timestamp": created.replace(minute=10),
                "side": "SELL",
                "token_in": "0xToken",
                "amount_in": Decimal(60),
                "usd_value": Decimal(9),
                "price": Decimal("0.15"),
                "metadata_json": {"ath_at": created.replace(minute=20).isoformat()},
            },
            {
                "wallet_address": "0xWallet",
                "signature": "0xMark",
                "timestamp": created.replace(minute=30),
                "side": "BUY",
                "token_out": "0xToken",
                "amount_out": Decimal(0),
                "usd_value": Decimal(0),
                "price": Decimal("0.20"),
                "graduated": True,
                "is_ath": True,
            },
        ],
        token_created_at=created,
    )
    buyer = buyers[0]
    assert buyer.buy_count == 2
    assert buyer.sell_count == 1
    assert buyer.total_buy == Decimal(10)
    assert buyer.total_sell == Decimal(9)
    assert buyer.realized_pnl == Decimal(3)
    assert buyer.unrealized_pnl == Decimal(4)
    assert buyer.sold_before_ath is True
    assert buyer.survived_to_graduation is True
    assert buyer.holding_duration == 1800

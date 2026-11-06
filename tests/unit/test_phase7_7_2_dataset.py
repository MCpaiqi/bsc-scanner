from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from smms.backtest.dataset import HistoricalDatasetBuilder
from smms.db.models import (
    Base,
    EarlyBuyer,
    MarketEvent,
    Pair,
    Token,
    TokenRiskSnapshot,
    TokenSmartMoneySnapshot,
    TradeEvent,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return Session(engine)


def _token(session: Session, address: str, outcome: str) -> None:
    session.add(Token(address=address, chain="bsc", creation_timestamp=T0, outcome_class=outcome))
    session.add(Pair(chain_id=56, address=f"pair-{address}", token0_address=address, token1_address="wbnb", creation_timestamp=T0))
    session.add(TradeEvent(chain="bsc", token_address=address, trader_address="wallet", tx_hash=f"tx-{address}", timestamp=T0, side="BUY", price=Decimal(1), price_usd=Decimal(1), pair_address=f"pair-{address}", market_cap=1000, liquidity=500, source="fixture"))


def test_survivorship_bias_keeps_all_tokens_in_original_universe() -> None:
    with _session() as session:
        for address, outcome in (("winner", "WINNER"), ("flat", "FLAT"), ("loser", "LOSER"), ("rug", "RUG")):
            _token(session, address, outcome)
        session.commit()

        rows, report = HistoricalDatasetBuilder(session).build(T0, T0 + timedelta(minutes=1))

        assert {row.token for row in rows} == {"winner", "flat", "loser", "rug"}
        assert report.tokens_eligible == 4


def test_future_facts_cannot_contaminate_a_past_row() -> None:
    with _session() as session:
        _token(session, "token", "RUG")
        session.add_all([
            MarketEvent(chain_id=56, event_type="SWAP", token_address="token", tx_hash="market-now", timestamp=T0, price=1, usd_value=10, pair_address="pair-token", metadata_json={"price_basis": "USD"}, source="fixture"),
            MarketEvent(chain_id=56, event_type="SWAP", token_address="token", tx_hash="market-future", timestamp=T0 + timedelta(minutes=1), price=99, usd_value=999, pair_address="pair-token", metadata_json={"price_basis": "USD"}, source="fixture"),
            TokenSmartMoneySnapshot(chain="bsc", token_address="token", smart_wallet_count=1, smart_money_buy_usd=10, smart_money_sell_usd=0, smart_money_net_flow=10, early_smart_buy_count=1, smart_money_score=10, confidence="LOW", confidence_score=20, data_status="OK", as_of=T0, score_version="v2"),
            TokenSmartMoneySnapshot(chain="bsc", token_address="token", smart_wallet_count=99, smart_money_buy_usd=999, smart_money_sell_usd=0, smart_money_net_flow=999, early_smart_buy_count=99, smart_money_score=99, confidence="HIGH", confidence_score=99, data_status="OK", as_of=T0 + timedelta(seconds=1), score_version="v2"),
            TokenRiskSnapshot(chain="bsc", token_address="token", contract_risk="LOW", liquidity_risk="LOW", holder_risk="LOW", behavior_risk="LOW", overall_risk="LOW", confidence="HIGH", evidence_json=[], dimensions_json={}, as_of=T0, risk_version="risk-v1"),
            TokenRiskSnapshot(chain="bsc", token_address="token", contract_risk="CRITICAL", liquidity_risk="CRITICAL", holder_risk="CRITICAL", behavior_risk="CRITICAL", overall_risk="CRITICAL", confidence="HIGH", evidence_json=["future rug"], dimensions_json={}, as_of=T0 + timedelta(seconds=1), risk_version="risk-v1"),
            EarlyBuyer(chain="bsc", wallet_address="early", token_address="token", first_buy_tx="early", first_buy_timestamp=T0, buy_rank=1),
            EarlyBuyer(chain="bsc", wallet_address="future", token_address="token", first_buy_tx="future", first_buy_timestamp=T0 + timedelta(seconds=1), buy_rank=2),
        ])
        session.commit()

        rows, _ = HistoricalDatasetBuilder(session).build(T0, T0)
        row = rows[0]

        assert row.market["price"] == "1"
        assert row.smart_money["score"] == Decimal("10.00")
        assert row.risk["level"] == "LOW"
        assert row.early_buyer["count"] == 1
        assert any(price["price"] == "99" for price in row.evaluation_price_path)


def test_materialized_dataset_contains_reproducibility_metadata() -> None:
    with _session() as session:
        _token(session, "materialized", "FLAT")
        session.commit()

        dataset, report = HistoricalDatasetBuilder(session).materialize(T0, T0, dataset_version="fixture-v1")

        assert dataset.dataset_version == "fixture-v1"
        assert dataset.config_hash
        assert dataset.report_json["rows_generated"] == report.rows_generated


def test_pair_created_after_discovery_does_not_make_token_eligible() -> None:
    with _session() as session:
        session.add(Token(address="late", chain="bsc", creation_timestamp=T0))
        session.add(Pair(chain_id=56, address="late-pair", token0_address="late", token1_address="wbnb", creation_timestamp=T0 + timedelta(seconds=1)))
        session.commit()

        rows, report = HistoricalDatasetBuilder(session).build(T0, T0)

        assert rows == []
        assert report.tokens_total == 1
        assert report.tokens_excluded == 1


def test_reversed_dataset_range_is_rejected() -> None:
    with _session() as session:
        try:
            HistoricalDatasetBuilder(session).build(T0 + timedelta(seconds=1), T0)
        except ValueError as exc:
            assert "before" in str(exc)
        else:
            raise AssertionError("reversed ranges must be rejected")

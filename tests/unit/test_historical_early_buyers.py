from datetime import UTC, datetime, timedelta
from decimal import Decimal

from smms.smart_money import (
    EarlyBuyerConfig,
    HistoricalTokenSnapshot,
    HistoricalWinnerConfig,
    discover_historical_winners,
    identify_early_buyers,
)
from smms.tokens import discover_winners

AS_OF = datetime(2026, 1, 1, tzinfo=UTC)


def test_historical_winners_respect_threshold_lookback_and_as_of_time() -> None:
    snapshots = [
        HistoricalTokenSnapshot("winner", "WIN", AS_OF - timedelta(days=10), Decimal(100), "100x+"),
        HistoricalTokenSnapshot("neutral", "MID", AS_OF - timedelta(days=10), Decimal(10), "10x-100x"),
        HistoricalTokenSnapshot("old", "OLD", AS_OF - timedelta(days=500), Decimal(500), "100x+"),
        HistoricalTokenSnapshot("future", "FUT", AS_OF + timedelta(days=1), Decimal(1000), "1000x+"),
        HistoricalTokenSnapshot("unknown", None, AS_OF - timedelta(days=5), None, "UNKNOWN"),
    ]
    result = discover_historical_winners(
        snapshots,
        as_of=AS_OF,
        config=HistoricalWinnerConfig(min_multiple=Decimal(50), lookback_days=365),
    )
    assert [token.address for token in result] == ["winner"]


def test_token_discovery_facade_returns_a_replayable_result() -> None:
    snapshots = [
        HistoricalTokenSnapshot("winner", "WIN", AS_OF - timedelta(days=1), Decimal(100), "100x+"),
    ]
    result = discover_winners(snapshots, as_of=AS_OF, min_multiple=Decimal(50))
    assert result.as_of == AS_OF
    assert result.config.min_multiple == Decimal(50)
    assert result.winners[0].address == "winner"


def test_early_buyers_only_include_target_token_buy_events_within_window() -> None:
    created = AS_OF
    transactions = [
        _tx("valid", "sig-1", created + timedelta(minutes=5), side="BUY"),
        _tx("transfer", "sig-2", created + timedelta(minutes=1), side="BUY", event_type="TRANSFER"),
        _tx("lp", "sig-3", created + timedelta(minutes=2), side="BUY", event_type="LP"),
        _tx("airdrop", "sig-4", created + timedelta(minutes=3), side="BUY", event_type="AIRDROP"),
        _tx("seller", "sig-5", created + timedelta(minutes=4), side="SELL"),
        _tx("late", "sig-6", created + timedelta(hours=25), side="BUY"),
        _tx("other", "sig-7", created + timedelta(minutes=1), side="BUY", token_out="OtherToken"),
    ]
    result = identify_early_buyers("Token111", transactions, token_created_at=created)
    assert [buyer.wallet_address for buyer in result] == ["valid"]


def test_early_buyer_ranking_is_unique_deterministic_and_keeps_bot_evidence() -> None:
    created = AS_OF
    transactions = [
        _tx("wallet-b", "sig-b", created + timedelta(minutes=2), usd_value="5"),
        _tx("wallet-a", "sig-a2", created + timedelta(minutes=1), usd_value="10"),
        _tx("wallet-a", "sig-a1", created + timedelta(seconds=30), usd_value="2"),
        _tx("wallet-a", "sig-a3", created + timedelta(minutes=4), usd_value="3"),
        _tx(
            "wallet-bot",
            "sig-bot",
            created + timedelta(minutes=3),
            usd_value="20",
            is_bot=True,
            bot_probability=Decimal("0.9"),
        ),
    ]
    result = identify_early_buyers(
        "Token111",
        transactions,
        token_created_at=created,
        config=EarlyBuyerConfig(top_n=3, max_entry_hours=24),
    )
    assert [buyer.wallet_address for buyer in result] == ["wallet-a", "wallet-b", "wallet-bot"]
    assert result[0].tx_signature == "sig-a1"
    assert result[0].total_buy_usd == Decimal(15)
    assert result[-1].is_bot is True
    assert result[-1].bot_probability == Decimal("0.9")


def _tx(
    wallet: str,
    signature: str,
    timestamp: datetime,
    *,
    side: str = "BUY",
    token_out: str = "Token111",
    event_type: str = "SWAP",
    usd_value: str = "1",
    is_bot: bool = False,
    bot_probability: Decimal | None = None,
) -> dict[str, object]:
    return {
        "wallet_address": wallet,
        "signature": signature,
        "timestamp": timestamp,
        "side": side,
        "token_out": token_out,
        "usd_value": Decimal(usd_value),
        "market_cap": Decimal(1000000),
        "is_bot": is_bot,
        "bot_probability": bot_probability,
        "metadata_json": {"event_type": event_type},
    }

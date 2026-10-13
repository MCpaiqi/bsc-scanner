from datetime import UTC, datetime, timedelta
from decimal import Decimal

from smms.smart_money import WalletProfileReplay, build_wallet_profile


def _tx(wallet: str, token: str, side: str, at: datetime, sig: str, amount: str, value: str, block: int):
    return {
        "wallet_address": wallet,
        "token_address": token,
        "side": side,
        "timestamp": at,
        "tx_hash": sig,
        "token_amount": Decimal(amount),
        "native_amount": Decimal(value),
        "price": Decimal(value) / Decimal(amount),
        "block_number": block,
    }


def test_wallet_profile_aggregates_positions_and_threshold_hits():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    trades = [
        _tx("w", "a", "BUY", start, "a1", "1", "10", 1),
        _tx("w", "a", "SELL", start + timedelta(seconds=4), "a2", "1", "20", 2),
        _tx("w", "b", "BUY", start + timedelta(hours=1), "b1", "1", "10", 3),
        _tx("w", "b", "SELL", start + timedelta(hours=1, seconds=4), "b2", "1", "5", 4),
    ]
    profile = build_wallet_profile("w", trades=trades)
    assert profile.total_tokens == 2
    assert profile.closed_positions == 2
    assert profile.wins == 1 and profile.losses == 1
    assert profile.threshold_hits["2x+"] == 1
    assert profile.median_multiple == Decimal("1.25")
    assert profile.confidence == "LOW"


def test_profile_replay_is_as_of_and_small_samples_are_low_confidence():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    trades = [_tx("w", "a", "BUY", start, "a1", "1", "10", 1), _tx("w", "a", "SELL", start + timedelta(seconds=1), "a2", "1", "12", 2)]
    profile = WalletProfileReplay().replay("w", trades=trades, as_of=start + timedelta(seconds=0.5))
    assert profile.sample_size == 1
    assert profile.confidence == "LOW"


def test_persisted_performance_after_as_of_is_not_used():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    performance = {
        "wallet_address": "w",
        "token_address": "a",
        "entry_time": start,
        "exit_time": start + timedelta(days=2),
        "total_buy": Decimal(10),
        "total_sell": Decimal(20),
        "realized_pnl": Decimal(10),
        "roi": Decimal(1),
        "multiple": Decimal(2),
        "position_size": Decimal(0),
    }
    profile = WalletProfileReplay().replay(
        "w", performances=[performance], as_of=start + timedelta(days=1)
    )
    assert profile.sample_size == 0

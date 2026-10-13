from datetime import UTC, datetime, timedelta
from decimal import Decimal

from smms.smart_money import EarlyBuyerConfig, identify_early_buyers


def tx(wallet, sig, t, side, amount, usd, price=None, block=1, **extra):
    return {"wallet_address": wallet, "signature": sig, "timestamp": t, "side": side,
            "token_out": "T" if side == "BUY" else None, "token_in": "T" if side == "SELL" else None,
            "amount": Decimal(amount) if amount is not None else None,
            "usd_value": Decimal(usd) if usd is not None else None,
            "price": Decimal(price) if price is not None else None, "block_number": block, **extra}


def test_position_fifo_partial_full_and_buy_sell_buy():
    t = datetime(2026, 1, 1, tzinfo=UTC)
    events = [tx("w", "b1", t, "BUY", "10", "100", "10"), tx("w", "s1", t + timedelta(seconds=1), "SELL", "4", "60", "15"), tx("w", "b2", t + timedelta(seconds=2), "BUY", "2", "30", "15"), tx("w", "s2", t + timedelta(seconds=3), "SELL", "8", "80", "10")]
    result = identify_early_buyers("T", events, token_created_at=t)
    assert result[0].current_balance == Decimal(0)
    assert result[0].realized_pnl == Decimal(10)


def test_decimal_missing_price_mc_and_duplicate_are_safe():
    t = datetime(2026, 1, 1, tzinfo=UTC)
    events = [tx("w", "b", t, "BUY", "1.000000000000000001", "2", None), tx("w", "b", t, "BUY", "1", "2", None)]
    result = identify_early_buyers("T", events, token_created_at=t, config=EarlyBuyerConfig(top_n=1))
    assert len(result) == 1
    assert result[0].market_cap_status == "UNKNOWN"
    assert result[0].current_balance == Decimal("1.000000000000000001")


def test_configurable_minute_and_block_windows():
    t = datetime(2026, 1, 1, tzinfo=UTC)
    events = [tx("a", "a", t + timedelta(seconds=30), "BUY", "1", "1", block=101), tx("b", "b", t + timedelta(minutes=2), "BUY", "1", "1", block=105)]
    assert [b.wallet_address for b in identify_early_buyers("T", events, token_created_at=t, token_created_block=100, config=EarlyBuyerConfig(window_type="minutes", window_value=1))] == ["a"]
    assert [b.wallet_address for b in identify_early_buyers("T", events, token_created_at=t, token_created_block=100, config=EarlyBuyerConfig(window_type="blocks", window_value=2))] == ["a"]

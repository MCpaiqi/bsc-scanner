"""Execution windows retain semantics while dense work stays linear after sorting."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from fractions import Fraction
from random import Random
from types import SimpleNamespace

import pytest

from smms.alpha import baseline

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def trade(seconds, quote, token):
    return SimpleNamespace(timestamp=T0 + timedelta(seconds=seconds), quote_amount=quote, token_amount=token)


def best(events, threshold="0.01", end=120):
    return baseline.max_rolling_vwap(events, start=T0, end=T0 + timedelta(seconds=end),
                                     min_quote_notional=Decimal(threshold))


def test_dense_same_timestamp_resolves_each_trade_only_once(monkeypatch):
    count = 160
    events = [trade(1, Decimal(i + 1), Decimal(1)) for i in range(count)]
    original, calls = baseline.swap_amounts_for_basis, []
    def resolve(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(baseline, "swap_amounts_for_basis", resolve)
    value, at = best(events)
    assert value == Decimal("80.5") and at == T0 + timedelta(seconds=1)
    assert len(calls) <= count * 2


def test_notional_gate_uses_exact_decimal_totals():
    events = [trade(1, Decimal("1e19"), Decimal(1)), trade(1, Decimal("1e-9"), Decimal(1))]
    threshold = Decimal("10000000000000000000.000000001")
    assert baseline.rolling_vwap(events, start=T0, end=T0 + timedelta(seconds=1), min_quote_notional=threshold) is not None
    assert best(events, str(threshold))[0] is not None


def test_tiny_remaining_trade_survives_expiry_of_a_large_trade_at_an_invalid_anchor():
    events = [trade(1, Decimal("1e19"), Decimal("1e19")),
              trade(2, Decimal("1e-9"), Decimal("1e-10")), trade(31, None, None)]
    with localcontext() as context:
        context.prec = 8
        assert best(events, "1e-10") == (Decimal(10), T0 + timedelta(seconds=31))


def test_equal_timestamp_trades_enter_the_window_together():
    assert best([trade(1, Decimal(1), Decimal(1)), trade(1, Decimal(9), Decimal(1))]) == (Decimal(5), T0 + timedelta(seconds=1))


def test_sparse_thirty_second_windows_can_fall_back_to_sixty():
    assert best([trade(1, Decimal(".006"), Decimal(".006")), trade(45, Decimal(".006"), Decimal(".006"))]) == (Decimal(1), T0 + timedelta(seconds=45))


def test_executable_thirty_second_window_precedes_a_better_sixty_second_window():
    assert best([trade(1, Decimal(".02"), Decimal(".02")), trade(45, Decimal(".009"), Decimal(".0009"))]) == (Decimal(1), T0 + timedelta(seconds=1))


def test_start_is_exclusive_end_inclusive_and_ties_keep_the_first_anchor():
    events = [trade(0, Decimal(100), Decimal(1)), trade(60, Decimal(1), Decimal(1)), trade(1, Decimal(1), Decimal(1))]
    assert best(iter(events), end=60) == (Decimal(1), T0 + timedelta(seconds=1))
    assert best([trade(121, Decimal(100), Decimal(1))]) == (None, None)


def reference(events, threshold):
    for width in [30, 60]:
        result = None
        for anchor in sorted({event.timestamp for event in events if T0 < event.timestamp <= T0 + timedelta(seconds=120)}):
            eligible = [event for event in events if max(T0, anchor - timedelta(seconds=width)) < event.timestamp <= anchor
                        and event.quote_amount is not None and event.token_amount is not None
                        and event.quote_amount > 0 and event.token_amount > 0]
            quote = sum((Fraction(event.quote_amount) for event in eligible), Fraction(0))
            token = sum((Fraction(event.token_amount) for event in eligible), Fraction(0))
            if quote < Fraction(threshold) or not token:
                continue
            ratio = quote / token
            value = Decimal(ratio.numerator) / Decimal(ratio.denominator)
            if result is None or value > result[0]:
                result = value, anchor
        if result is not None:
            return result
    return None, None


@pytest.mark.parametrize("seed", range(10))
def test_windows_match_an_independent_fraction_reference(seed):
    random = Random(seed)
    amounts = [Decimal(".001"), Decimal(".006"), Decimal(".02"), Decimal(1), Decimal("1e19")]
    events = [trade(random.randrange(0, 125), random.choice(amounts), random.choice(amounts)) for _ in range(35)]
    events += [trade(31, None, None), trade(60, Decimal(0), Decimal(1))]
    random.shuffle(events)
    threshold = Decimal(".01")
    assert best(events, str(threshold)) == reference(events, threshold)


def test_raw_uint_amounts_keep_their_basis_and_human_notional_gate():
    from smms.db.models import Pair, TradeEvent

    pair = Pair(chain_id=56, address="raw-pair", token0_address="target", token1_address="quote", quote_token="quote")
    unit = 1 << 252
    events = [TradeEvent(chain="bsc", token_address="target", pair_address=pair.address,
        timestamp=T0 + timedelta(seconds=1), side="BUY", quote_amount=Decimal(2), token_amount=None,
        metadata_json={"amounts_raw": ["0", str(ratio * unit), str(unit), "0"]}) for ratio in [2, 4]]
    kwargs = {"start": T0, "end": T0 + timedelta(seconds=2), "pair": pair,
              "price_basis": baseline.PRICE_BASIS_RAW}
    assert baseline.max_rolling_vwap(events, min_quote_notional=Decimal(3), **kwargs)[0] == 3
    assert baseline.max_rolling_vwap(events, min_quote_notional=Decimal(5), **kwargs) == (None, None)

"""Point-in-time wallet x token calculations for Flap trades."""

from collections import defaultdict
from collections.abc import Iterable
from decimal import Decimal

from smms.db.models import TradeEvent, WalletTokenPerformance


def _ordered(events: Iterable[TradeEvent]) -> list[TradeEvent]:
    return sorted(
        events,
        key=lambda event: (
            event.block_number if event.block_number is not None else 2**63 - 1,
            event.event_index,
        ),
    )


def calculate_wallet_token_performance(
    events: Iterable[TradeEvent],
) -> list[WalletTokenPerformance]:
    """Calculate conservative weighted-average-cost positions from trade facts."""

    grouped: dict[tuple[str, str, str], list[TradeEvent]] = defaultdict(list)
    for event in events:
        grouped[(event.chain, event.trader_address, event.token_address)].append(event)

    results: list[WalletTokenPerformance] = []
    for (chain, wallet_address, token_address), wallet_events in grouped.items():
        ordered = _ordered(wallet_events)
        def side_value(event: TradeEvent) -> str:
            side = getattr(event.side, "value", event.side)
            return str(side).upper()

        first_buy = next((event for event in ordered if side_value(event) == "BUY"), None)
        first_event = ordered[0]
        entry = first_buy or first_event
        inventory = Decimal(0)
        cost_basis = Decimal(0)
        realized = Decimal(0)
        total_buy = Decimal(0)
        total_sell = Decimal(0)
        last_price: Decimal | None = None
        last_sell = None
        max_pnl: Decimal | None = None
        max_multiple: Decimal | None = None
        amounts_complete = True
        for event in ordered:
            if event.price is not None:
                last_price = event.price
            if event.token_amount is None or event.native_amount is None:
                # A missing leg makes every cumulative position/PnL value after
                # it unknowable. Never turn an unavailable amount into zero.
                amounts_complete = False
                continue
            if not amounts_complete:
                continue
            token_amount = event.token_amount
            native_amount = event.native_amount
            if side_value(event) == "BUY":
                inventory += token_amount
                cost_basis += native_amount
                total_buy += native_amount
            elif side_value(event) == "SELL":
                # A sell without a fully observed position may represent an
                # entry before the requested range, a transfer, or an
                # unobserved allocation. Its proceeds are not a realized PnL.
                if inventory <= 0 or token_amount <= 0 or token_amount > inventory:
                    amounts_complete = False
                    continue
                total_sell += native_amount
                last_sell = event
                sold = token_amount
                average_cost = cost_basis / inventory
                proceeds = native_amount * sold / token_amount
                realized += proceeds - (average_cost * sold)
                inventory -= sold
                cost_basis -= average_cost * sold
            mark = (last_price * inventory - cost_basis) if last_price is not None else None
            current_pnl = realized + mark if mark is not None else realized
            max_pnl = current_pnl if max_pnl is None else max(max_pnl, current_pnl)
            if total_buy > 0:
                current_multiple = Decimal(1) + (current_pnl / total_buy)
                max_multiple = (
                    current_multiple
                    if max_multiple is None
                    else max(max_multiple, current_multiple)
                )

        if amounts_complete:
            position_size = inventory
            total_buy_value: Decimal | None = total_buy
            total_sell_value: Decimal | None = total_sell
            realized_value: Decimal | None = realized
            unrealized = (
                last_price * inventory - cost_basis if last_price is not None else None
            )
            invested = total_buy
            total_pnl = realized + (unrealized or Decimal(0))
            roi = total_pnl / invested if invested > 0 else None
            exit_time = last_sell.timestamp if inventory <= 0 and last_sell is not None else None
            duration_seconds = (
                int((exit_time - entry.timestamp).total_seconds())
                if exit_time is not None
                else None
            )
            max_pnl_value = max_pnl
            threshold_multiple = max_multiple
        else:
            position_size = None
            total_buy_value = None
            total_sell_value = None
            realized_value = None
            unrealized = None
            roi = None
            exit_time = None
            duration_seconds = None
            max_pnl_value = None
            threshold_multiple = None
        multiple = (Decimal(1) + roi) if roi is not None else None
        results.append(
            WalletTokenPerformance(
                chain=chain,
                launchpad=entry.launchpad,
                wallet_address=wallet_address,
                token_address=token_address,
                entry_time=entry.timestamp,
                entry_market_cap=entry.market_cap,
                entry_price=entry.price,
                position_size=position_size,
                total_buy=total_buy_value,
                total_sell=total_sell_value,
                realized_pnl=realized_value,
                unrealized_pnl=unrealized,
                max_pnl=max_pnl_value,
                exit_time=exit_time,
                holding_duration=duration_seconds,
                holding_duration_seconds=duration_seconds,
                roi=roi,
                multiple=multiple,
                reached_2x=threshold_multiple is not None and threshold_multiple >= 2,
                reached_5x=threshold_multiple is not None and threshold_multiple >= 5,
                reached_10x=threshold_multiple is not None and threshold_multiple >= 10,
                reached_50x=threshold_multiple is not None and threshold_multiple >= 50,
                reached_100x=threshold_multiple is not None and threshold_multiple >= 100,
            )
        )
    return results


__all__ = ["calculate_wallet_token_performance"]

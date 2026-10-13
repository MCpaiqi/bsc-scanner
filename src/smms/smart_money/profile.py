"""Wallet-level historical intelligence.

The profile builder intentionally accepts both ORM rows and plain mappings.  It
does not alter the smart-money score contract; callers may feed the resulting
metrics into :mod:`smms.smart_money.scoring` when desired.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from itertools import pairwise
from statistics import median
from typing import Any

from smms.chains.constants import DEFAULT_CHAIN, normalize_address, validate_bsc_chain


def _get(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    row_mapping = getattr(value, "_mapping", None)
    if isinstance(row_mapping, Mapping):
        return row_mapping.get(name, default)
    return getattr(value, name, default)


def _dec(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    except (TypeError, ValueError):
        return None


def _ratio(numerator: Decimal, denominator: Decimal) -> Decimal:
    return numerator / denominator if denominator else Decimal(0)


@dataclass(frozen=True)
class WalletProfile:
    wallet: str
    chain: str
    total_tokens: int = 0
    closed_positions: int = 0
    open_positions: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: Decimal = Decimal(0)
    total_invested: Decimal = Decimal(0)
    total_returned: Decimal = Decimal(0)
    realized_pnl: Decimal = Decimal(0)
    unrealized_pnl: Decimal = Decimal(0)
    roi: Decimal = Decimal(0)
    median_roi: Decimal = Decimal(0)
    median_multiple: Decimal = Decimal(0)
    best_multiple: Decimal = Decimal(0)
    worst_multiple: Decimal = Decimal(0)
    early_entry_rate: Decimal = Decimal(0)
    consistency: Decimal = Decimal(0)
    rug_exposure: Decimal = Decimal(0)
    bot_probability: Decimal = Decimal(0)
    wash_probability: Decimal = Decimal(0)
    confidence: str = "LOW"
    number_trades: int = 0
    sample_size: int = 0
    average_win: Decimal = Decimal(0)
    average_loss: Decimal = Decimal(0)
    profit_factor: Decimal = Decimal(0)
    max_drawdown: Decimal = Decimal(0)
    average_position_size: Decimal = Decimal(0)
    concentration: Decimal = Decimal(0)
    liquidity_dependency: Decimal = Decimal(0)
    # Phase 8 behaviour dimensions (all derived from observations at ``as_of``).
    entry_timing: Decimal = Decimal(0)
    average_entry_delay_seconds: Decimal = Decimal(0)
    average_holding_period_seconds: Decimal = Decimal(0)
    holding_period: Decimal = Decimal(0)
    exit_efficiency: Decimal = Decimal(0)
    realized_roi: Decimal = Decimal(0)
    unrealized_roi: Decimal = Decimal(0)
    win_streak: int = 0
    loss_streak: int = 0
    chain_activity: int = 0
    threshold_hits: dict[str, int] = field(default_factory=dict)
    behavioral_indicators: dict[str, Any] = field(default_factory=dict)

    @property
    def wash_trading_probability(self) -> Decimal:
        """Alias used by the existing scoring contract."""
        return self.wash_probability

    @property
    def smart_money_score(self) -> Decimal:
        """Compatibility score calculated by the existing scoring contract."""
        from .scoring import SmartMoneyMetrics, calculate_smart_money_score

        high_multiple_rate = (
            _ratio(Decimal(self.threshold_hits.get("5x+", 0)), Decimal(self.sample_size))
            if self.sample_size
            else Decimal(0)
        )
        return calculate_smart_money_score(
            SmartMoneyMetrics(
                win_rate=self.win_rate,
                avg_roi=self.roi,
                median_roi=self.median_roi,
                risk_adjusted_return=self.roi / (Decimal(1) + self.max_drawdown)
                if self.max_drawdown >= 0
                else self.roi,
                early_entry_rate=self.early_entry_rate,
                high_multiple_rate=high_multiple_rate,
                consistency_rate=self.consistency,
                rug_exposure=self.rug_exposure,
                bot_probability=self.bot_probability,
                wash_trading_probability=self.wash_probability,
                sample_size=self.sample_size,
                data_completeness=Decimal("0.8") if self.sample_size else Decimal(0),
                trade_quality=Decimal(1) - (self.bot_probability + self.wash_probability) / Decimal(2),
            )
        ).score

    @property
    def smart_money_score_result(self):
        from .scoring import SmartMoneyMetrics, calculate_smart_money_score
        high_multiple_rate = _ratio(Decimal(self.threshold_hits.get("5x+", 0)), Decimal(self.sample_size)) if self.sample_size else Decimal(0)
        return calculate_smart_money_score(SmartMoneyMetrics(
            win_rate=self.win_rate, avg_roi=self.roi, median_roi=self.median_roi,
            risk_adjusted_return=self.roi / (Decimal(1) + self.max_drawdown) if self.max_drawdown >= 0 else self.roi,
            early_entry_rate=self.early_entry_rate, high_multiple_rate=high_multiple_rate,
            consistency_rate=self.consistency, rug_exposure=self.rug_exposure,
            bot_probability=self.bot_probability, wash_trading_probability=self.wash_probability,
            sample_size=self.sample_size, data_completeness=Decimal("0.8") if self.sample_size else Decimal(0),
            trade_quality=Decimal(1) - (self.bot_probability + self.wash_probability) / Decimal(2),
        ))

    @property
    def avg_roi(self) -> Decimal:
        """Alias matching ``WalletPerformance.avg_roi`` and scoring metrics."""
        return self.roi

    @property
    def trade_count(self) -> int:
        return self.number_trades

    @property
    def number_of_trades(self) -> int:
        return self.number_trades

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _normalise_trades(
    trades: Iterable[Any], wallet: str | None = None, *, chain: str = DEFAULT_CHAIN
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in trades:
        trader = _get(row, "wallet_address", _get(row, "trader_address"))
        if wallet and trader and normalize_address(str(trader), chain=chain) != normalize_address(wallet, chain=chain):
            continue
        raw_side = _get(row, "side", "")
        side = str(getattr(raw_side, "value", raw_side)).upper()
        token = _get(row, "token_address", _get(row, "token"))
        timestamp = _utc(_get(row, "timestamp", _get(row, "block_timestamp")))
        if not token or side not in {"BUY", "SELL"} or timestamp is None:
            continue
        # early_buyers expects usd_value; native_amount is the canonical value
        # available on TradeEvent rows and is a safe fallback for accounting.
        usd_value = _get(row, "usd_value")
        if usd_value is None:
            usd_value = _get(row, "native_amount")
        result.append({
            "wallet_address": str(trader or wallet or ""),
            "token_address": str(token),
            "side": side,
            "timestamp": timestamp,
            "signature": _get(row, "signature", _get(row, "tx_hash", "")),
            "event_index": _get(row, "event_index", 0),
            "block_number": _get(row, "block_number"),
            "amount": _get(row, "amount", _get(row, "token_amount")),
            "token_amount": _get(row, "token_amount", _get(row, "amount")),
            "usd_value": usd_value,
            "price": _get(row, "price"),
            "market_cap": _get(row, "market_cap"),
            "liquidity": _get(row, "liquidity"),
            "metadata_json": _get(row, "metadata_json", _get(row, "metadata", {})) or {},
        })
    return result


def _performance_from_trades(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Conservative WAC accounting for raw normalized trade dictionaries."""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for tx in trades:
        grouped[tx["token_address"]].append(tx)
    output: list[dict[str, Any]] = []
    for token, events in grouped.items():
        events.sort(key=lambda x: (x["timestamp"], x.get("block_number") or 0, str(x.get("signature", ""))))
        inventory = Decimal(0); cost = Decimal(0); invested = Decimal(0); returned = Decimal(0); realized = Decimal(0); complete = True
        first = next((e for e in events if e["side"] == "BUY"), events[0]); last_price = None; max_pnl = None; peak = Decimal(0); drawdown = Decimal(0)
        for e in events:
            qty, usd = _dec(e.get("token_amount")), _dec(e.get("usd_value"))
            if e.get("price") is not None: last_price = _dec(e.get("price"))
            if e["side"] == "BUY":
                if qty is None or usd is None:
                    complete = False
                else:
                    inventory += qty; cost += usd; invested += usd
            elif e["side"] == "SELL":
                if qty is None or usd is None or inventory <= 0 or qty <= 0 or qty > inventory:
                    complete = False
                else:
                    avg = cost / inventory if inventory else Decimal(0); realized += usd - avg * qty; returned += usd; inventory -= qty; cost -= avg * qty
            mark = (last_price * inventory - cost) if last_price is not None else Decimal(0)
            pnl = realized + mark
            peak = max(peak, pnl); drawdown = max(drawdown, peak - pnl); max_pnl = max(max_pnl or pnl, pnl)
        unrealized = (last_price * inventory - cost) if last_price is not None else None
        closed = complete and inventory <= 0 and invested > 0
        first_meta = first.get("metadata_json", {}) if isinstance(first.get("metadata_json", {}), Mapping) else {}
        if not complete:
            # Preserve known capital while marking the position outcome unknown.
            output.append({"token_address": token, "entry_time": first["timestamp"], "total_buy": invested or None, "total_sell": returned or None, "realized_pnl": None, "unrealized_pnl": None, "max_pnl": None, "roi": None, "multiple": None, "position_size": None, "exit_time": None, "max_drawdown": None, "closed": False, "entry_market_cap": first.get("market_cap"), "entry_delay_seconds": _get(first_meta, "entry_delay_seconds"), "token_created_at": first_meta.get("token_created_at", first_meta.get("created_at")), "liquidity_added_at": first_meta.get("liquidity_added_at"), "price_discovery_at": first_meta.get("price_discovery_at"), "outcome_class": first_meta.get("outcome_class")})
            continue
        total_pnl = realized + (unrealized or Decimal(0))
        # An open position without a price mark has an unknown ROI, not a loss.
        roi = _ratio(total_pnl, invested) if invested and (closed or unrealized is not None) else None
        output.append({"token_address": token, "entry_time": first["timestamp"], "total_buy": invested or None, "total_sell": returned or None, "realized_pnl": realized, "unrealized_pnl": unrealized, "max_pnl": max_pnl, "roi": roi if invested else None, "multiple": (Decimal(1) + roi) if roi is not None else None, "position_size": inventory, "exit_time": events[-1]["timestamp"] if closed else None, "max_drawdown": _ratio(drawdown, invested), "closed": closed, "entry_market_cap": first.get("market_cap"), "entry_delay_seconds": _get(first_meta, "entry_delay_seconds"), "token_created_at": first_meta.get("token_created_at", first_meta.get("created_at")), "liquidity_added_at": first_meta.get("liquidity_added_at"), "price_discovery_at": first_meta.get("price_discovery_at"), "outcome_class": first_meta.get("outcome_class")})
    return output


def build_wallet_profile(
    wallet: str,
    chain: str = DEFAULT_CHAIN,
    *,
    trades: Iterable[Any] | None = None,
    performances: Iterable[Any] | None = None,
    token_metadata: Mapping[str, Any] | None = None,
    as_of: datetime | None = None,
    early_window_hours: int = 24,
) -> WalletProfile:
    """Build a point-in-time profile from raw trades or WalletTokenPerformance rows."""
    chain = validate_bsc_chain(chain)
    normalized = _normalise_trades(trades or (), wallet, chain=chain)
    normalized.sort(key=lambda t: (t["timestamp"], t.get("block_number") or 0, str(t.get("signature", "")), t.get("event_index", 0)))
    cutoff = _utc(as_of)
    if cutoff is not None:
        normalized = [t for t in normalized if t["timestamp"] <= cutoff]
    supplied_performances = performances is not None
    rows = [_row_to_dict(p) for p in performances or ()]
    if not rows and normalized:
        rows = _performance_from_trades(normalized)
    rows = [
        r
        for r in rows
        if not r.get("wallet_address")
        or normalize_address(str(r.get("wallet_address")), chain=chain)
        == normalize_address(wallet, chain=chain)
    ]
    if cutoff is not None and supplied_performances:
        rows = [r for r in rows if _observed_by(r, cutoff)]
    total = len(rows); closed = sum(1 for r in rows if _is_closed(r)); open_count = total - closed
    rois = [_dec(r.get("roi")) for r in rows]; rois = [x for x in rois if x is not None]
    multiples = [_dec(r.get("multiple")) or (Decimal(1) + x) for r, x in ((r, _dec(r.get("roi"))) for r in rows) if _dec(r.get("multiple")) is not None or x is not None]
    wins = sum(1 for x in rois if x > 0); losses = sum(1 for x in rois if x <= 0)
    invested = sum((_dec(r.get("total_buy", r.get("total_invested"))) or Decimal(0) for r in rows), Decimal(0))
    returned = sum((_dec(r.get("total_sell", r.get("total_returned"))) or Decimal(0) for r in rows), Decimal(0))
    realized = sum((_dec(r.get("realized_pnl")) or Decimal(0) for r in rows), Decimal(0)); unrealized = sum((_dec(r.get("unrealized_pnl")) or Decimal(0) for r in rows), Decimal(0))
    pnl_values: list[Decimal] = []
    for row in rows:
        realized_value = _dec(row.get("realized_pnl"))
        unrealized_value = _dec(row.get("unrealized_pnl"))
        if realized_value is not None or unrealized_value is not None:
            pnl_values.append((realized_value or Decimal(0)) + (unrealized_value or Decimal(0)))
    positive = [x for x in pnl_values if x > 0]; negative = [x for x in pnl_values if x < 0]
    avg_win = _ratio(sum(positive, Decimal(0)), Decimal(len(positive))) if positive else Decimal(0)
    avg_loss = _ratio(sum(negative, Decimal(0)), Decimal(len(negative))) if negative else Decimal(0)
    gross_loss = abs(sum(negative, Decimal(0))); profit_factor = _ratio(sum(positive, Decimal(0)), gross_loss)
    early = 0
    metadata = token_metadata or {}
    for r in rows:
        delay = _dec(r.get("entry_delay_seconds"))
        if delay is None:
            info = metadata.get(str(r.get("token_address")), {}) or {}
            entry = _utc(r.get("entry_time")); created = _utc(r.get("token_created_at")) or _utc(_get(info, "created_at", _get(info, "creation_time")))
            liq = _utc(r.get("liquidity_added_at")) or _utc(_get(info, "liquidity_added_at")); discovery = _utc(r.get("price_discovery_at")) or _utc(_get(info, "price_discovery_at"))
            base = created or liq or discovery
            delay = Decimal((entry - base).total_seconds()) if entry and base and entry >= base else None
        if delay is not None and delay <= early_window_hours * 3600: early += 1
    rugs = sum(1 for r in rows if str(r.get("outcome_class", metadata.get(str(r.get("token_address")), {}).get("outcome_class", ""))).upper() in {"RUG", "RUGGED", "RUG_PULL"})
    threshold_hits: dict[str, int] = {}
    for n in (1, 2, 5, 10, 20, 50, 100):
        flag = f"reached_{n}x"
        # Historical rows predating ``multiple`` may still carry materialized
        # threshold flags; retain those observations instead of dropping them.
        threshold_hits[f"{n}x+"] = sum(
            1 for r in rows
            if ((_dec(r.get("multiple")) is not None and _dec(r.get("multiple")) >= n)
                or (n != 1 and bool(r.get(flag))))
        )
    threshold_hits["1x+"] = max(threshold_hits["1x+"], sum(1 for m in multiples if m >= 1))
    number_trades = len(normalized) if normalized else sum(int(_get(r, "trade_count", 0) or 0) for r in rows)
    indicators = _behavioral_indicators(normalized)
    bot_probability = indicators["bot_probability"]; wash_probability = indicators["wash_probability"]
    max_dd = _portfolio_drawdown(normalized, invested)
    if max_dd == 0:
        max_dd = max((_dec(r.get("max_drawdown")) or Decimal(0) for r in rows), default=Decimal(0))
    max_dd = abs(max_dd)
    if invested and max_dd > 1:
        max_dd = max_dd / invested
    max_dd = min(max_dd, Decimal(1))
    avg_size = _ratio(invested, Decimal(total))
    concentration = Decimal(0)
    if invested:
        concentration = max((_dec(r.get("total_buy", r.get("total_invested"))) or Decimal(0) for r in rows), default=Decimal(0)) / invested
    liquidity_dependency = _liquidity_dependency(normalized)
    consistency = _consistency(rois)
    holding = [_utc(r.get("exit_time")) - _utc(r.get("entry_time")) for r in rows if _utc(r.get("exit_time")) and _utc(r.get("entry_time")) and _utc(r.get("exit_time")) >= _utc(r.get("entry_time"))]
    holding_seconds = [Decimal(str(x.total_seconds())) for x in holding]
    avg_holding = _ratio(sum(holding_seconds, Decimal(0)), Decimal(len(holding_seconds)))
    realized_base = invested or Decimal(0)
    realized_roi = _ratio(realized, realized_base)
    unrealized_roi = _ratio(unrealized, realized_base)
    # Exit efficiency is captured profit divided by the best observed profit when
    # both are available; unknown marks remain neutral rather than losses.
    exit_efficiency = _ratio(realized, sum((_dec(r.get("max_pnl")) or Decimal(0) for r in rows), Decimal(0))) if rows else Decimal(0)
    ordered_outcomes = [x > 0 for x in rois]
    win_streak = loss_streak = current_win = current_loss = 0
    for outcome in ordered_outcomes:
        if outcome:
            current_win += 1; current_loss = 0; win_streak = max(win_streak, current_win)
        else:
            current_loss += 1; current_win = 0; loss_streak = max(loss_streak, current_loss)
    entry_timing = _ratio(Decimal(early), Decimal(total))
    delays = [_dec(r.get("entry_delay_seconds")) for r in rows if _dec(r.get("entry_delay_seconds")) is not None]
    average_delay = _ratio(sum(delays, Decimal(0)), Decimal(len(delays)))
    return WalletProfile(wallet=wallet, chain=chain, total_tokens=total, closed_positions=closed, open_positions=open_count, wins=wins, losses=losses, win_rate=_ratio(Decimal(wins), Decimal(wins + losses)), total_invested=invested, total_returned=returned, realized_pnl=realized, unrealized_pnl=unrealized, roi=_ratio(realized + unrealized, invested), median_roi=Decimal(str(median(rois))) if rois else Decimal(0), median_multiple=Decimal(str(median(multiples))) if multiples else Decimal(0), best_multiple=max(multiples, default=Decimal(0)), worst_multiple=min(multiples, default=Decimal(0)), early_entry_rate=entry_timing, consistency=consistency, rug_exposure=_ratio(Decimal(rugs), Decimal(total)), bot_probability=bot_probability, wash_probability=wash_probability, confidence="HIGH" if total >= 10 else ("MEDIUM" if total >= 4 else "LOW"), number_trades=number_trades, sample_size=total, average_win=avg_win, average_loss=avg_loss, profit_factor=profit_factor, max_drawdown=max_dd, average_position_size=avg_size, concentration=concentration, liquidity_dependency=liquidity_dependency, entry_timing=entry_timing, average_entry_delay_seconds=average_delay, average_holding_period_seconds=avg_holding, holding_period=avg_holding, exit_efficiency=max(Decimal(0), min(Decimal(1), exit_efficiency)), realized_roi=realized_roi, unrealized_roi=unrealized_roi, win_streak=win_streak, loss_streak=loss_streak, chain_activity=number_trades, threshold_hits=threshold_hits, behavioral_indicators=indicators)


def _row_to_dict(row: Any) -> dict[str, Any]:
    if isinstance(row, Mapping): return dict(row)
    row_mapping = getattr(row, "_mapping", None)
    if isinstance(row_mapping, Mapping): return dict(row_mapping)
    names = ("wallet_address", "token_address", "entry_time", "exit_time", "last_trade_timestamp", "total_buy", "total_sell", "total_invested", "total_returned", "realized_pnl", "unrealized_pnl", "max_pnl", "roi", "multiple", "position_size", "max_drawdown", "entry_delay_seconds", "token_created_at", "liquidity_added_at", "price_discovery_at", "outcome_class", "trade_count", "closed")
    return {name: getattr(row, name, None) for name in names}


def _is_closed(row: Mapping[str, Any]) -> bool:
    if "closed" in row:
        return bool(row["closed"])
    if row.get("exit_time") is not None: return True
    size = _dec(row.get("position_size")); return size is not None and size <= 0


def _observed_by(row: Mapping[str, Any], cutoff: datetime) -> bool:
    """Accept a persisted snapshot only when its final observation is known."""
    observed = _utc(row.get("exit_time")) or _utc(row.get("last_trade_timestamp"))
    return observed is not None and observed <= cutoff


def _portfolio_drawdown(trades: list[dict[str, Any]], invested: Decimal) -> Decimal:
    if not trades or not invested:
        return Decimal(0)
    states: dict[str, dict[str, Decimal | None]] = {}
    peak = Decimal(0)
    drawdown = Decimal(0)
    for event in trades:
        token = event["token_address"]
        state = states.setdefault(token, {"inventory": Decimal(0), "cost": Decimal(0), "realized": Decimal(0), "price": None})
        qty, usd, price = _dec(event.get("token_amount")), _dec(event.get("usd_value")), _dec(event.get("price"))
        if price is not None:
            state["price"] = price
        if event["side"] == "BUY" and qty is not None and usd is not None:
            state["inventory"] = (state["inventory"] or Decimal(0)) + qty
            state["cost"] = (state["cost"] or Decimal(0)) + usd
        elif event["side"] == "SELL" and qty is not None and usd is not None and qty > 0 and qty <= (state["inventory"] or Decimal(0)):
            inventory = state["inventory"] or Decimal(0)
            average = (state["cost"] or Decimal(0)) / inventory if inventory else Decimal(0)
            state["realized"] = (state["realized"] or Decimal(0)) + usd - average * qty
            state["inventory"] = inventory - qty
            state["cost"] = (state["cost"] or Decimal(0)) - average * qty
        equity = Decimal(0)
        unknown_mark = False
        for current in states.values():
            if (current["inventory"] or Decimal(0)) and current["price"] is None:
                unknown_mark = True
                break
            mark = (current["price"] or Decimal(0)) * (current["inventory"] or Decimal(0)) - (current["cost"] or Decimal(0))
            equity += (current["realized"] or Decimal(0)) + mark
        if unknown_mark:
            continue
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return _ratio(drawdown, invested)


def _consistency(rois: list[Decimal]) -> Decimal:
    if not rois: return Decimal(0)
    if len(rois) == 1: return Decimal(0)
    positive = sum(1 for x in rois if x > 0) / len(rois)
    # Outcome repeatability, deliberately bounded by sample count and separate
    # from win_rate so a single outlier cannot dominate aggregate ROI.
    return Decimal(str(max(positive, 1 - positive)))


def _behavioral_indicators(trades: list[dict[str, Any]]) -> dict[str, Any]:
    if not trades: return {"trade_frequency_per_hour": Decimal(0), "same_size_buy_rate": Decimal(0), "same_block_rate": Decimal(0), "immediate_buy_sell_rate": Decimal(0), "synchronized_wallet_activity": False, "correlation_indicators": {}, "cluster_id": None, "bot_probability": Decimal(0), "wash_probability": Decimal(0)}
    buys = [t for t in trades if t["side"] == "BUY"]; sizes = [_dec(t.get("usd_value")) for t in buys]; sizes = [x for x in sizes if x is not None]
    same_size = sum(1 for x in sizes if sizes.count(x) > 1) / len(sizes) if sizes else 0
    blocks = [t.get("block_number") for t in trades if t.get("block_number") is not None]
    block_counts = Counter(blocks)
    same_block = (sum(count for count in block_counts.values() if count > 1) / len(blocks)) if blocks else 0
    immediate = 0
    for a, b in pairwise(trades):
        if a["token_address"] == b["token_address"] and a["side"] != b["side"] and (b["timestamp"] - a["timestamp"]).total_seconds() <= 60: immediate += 1
    immediate_rate = immediate / max(1, len(trades) - 1)
    span = (trades[-1]["timestamp"] - trades[0]["timestamp"]).total_seconds(); freq = len(trades) / (span / 3600) if span > 0 else Decimal(len(trades))
    bot = min(Decimal(1), Decimal(str(0.4 * min(float(freq) / 100, 1) + 0.3 * same_size + 0.1 * immediate_rate)))
    wash = min(Decimal(1), Decimal(str(0.6 * same_size + 0.4 * immediate_rate)))
    synchronized = any(
        bool((t.get("metadata_json") or {}).get("synchronized_wallet_activity"))
        for t in trades
        if isinstance(t.get("metadata_json"), Mapping)
    )
    cluster_ids = [str((t.get("metadata_json") or {}).get("cluster_id")) for t in trades if isinstance(t.get("metadata_json"), Mapping) and (t.get("metadata_json") or {}).get("cluster_id")]
    cluster_id = Counter(cluster_ids).most_common(1)[0][0] if cluster_ids else None
    return {"trade_frequency_per_hour": Decimal(str(freq)), "same_size_buy_rate": Decimal(str(same_size)), "same_block_rate": Decimal(str(same_block)), "immediate_buy_sell_rate": Decimal(str(immediate_rate)), "synchronized_wallet_activity": synchronized, "correlation_indicators": {"same_block_rate": Decimal(str(same_block)), "synchronized_wallet_activity": synchronized}, "cluster_id": cluster_id, "bot_probability": bot, "wash_probability": wash}


def _liquidity_dependency(trades: list[dict[str, Any]]) -> Decimal:
    if not trades: return Decimal(0)
    known = [_dec(t.get("liquidity")) for t in trades]; known = [x for x in known if x is not None and x > 0]
    if not known: return Decimal(0)
    return Decimal(str(sum(1 for t in trades if _dec(t.get("liquidity")) is not None) / len(trades)))


class WalletProfileReplay:
    """Small facade for deterministic as-of historical replay."""
    def __init__(self, *, chain: str = DEFAULT_CHAIN) -> None: self.chain = validate_bsc_chain(chain)
    def replay(self, wallet: str, *, trades: Iterable[Any] = (), performances: Iterable[Any] | None = None, as_of: datetime | None = None, **kwargs: Any) -> WalletProfile:
        cutoff = _utc(as_of)
        if cutoff is not None:
            trades = [t for t in trades if (_utc(_get(t, "timestamp", _get(t, "block_timestamp"))) or cutoff) <= cutoff]
            if performances is not None:
                performances = [
                    p for p in performances
                    if _observed_by(
                        _row_to_dict(p) if not isinstance(p, Mapping) else p,
                        cutoff,
                    )
                ]
        return build_wallet_profile(wallet, self.chain, trades=trades, performances=performances, as_of=as_of, **kwargs)


calculate_wallet_profile = build_wallet_profile
aggregate_wallet_profile = build_wallet_profile

__all__ = ["WalletProfile", "WalletProfileReplay", "aggregate_wallet_profile", "build_wallet_profile", "calculate_wallet_profile"]

"""Replayable early buyer discovery and Decimal position accounting."""
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

_NON_TRADE_EVENTS = {"TRANSFER", "LP", "AIRDROP", "INTERNAL_ROUTING", "UNKNOWN"}

@dataclass(frozen=True)
class EarlyBuyerConfig:
    top_n: int = 20
    max_entry_hours: int = 24
    window_type: str = "buyers"
    window_value: int | None = None
    @property
    def effective_window_value(self) -> int:
        return self.window_value if self.window_value is not None else (self.top_n if self.window_type in {"buyers", "blocks"} else 5)

@dataclass(frozen=True)
class PositionSnapshot:
    cumulative_bought: Decimal
    cumulative_sold: Decimal
    current_balance: Decimal
    total_invested: Decimal | None
    total_returned: Decimal | None
    realized_pnl: Decimal | None
    unrealized_pnl: Decimal | None
    roi: Decimal | None
    current_price: Decimal | None
    current_market_cap: Decimal | None
    last_trade_timestamp: datetime | None
    last_trade_block: int | None
    reorg_corrected: bool = False

@dataclass(frozen=True)
class EarlyBuyer:
    wallet_address: str
    first_buy_time: datetime
    total_buy_usd: Decimal = Decimal(0)
    entry_market_cap: Decimal | None = None
    tx_signature: str = ""
    first_buy_block: int | None = None
    token_address: str | None = None
    token_age_at_buy: int | None = None
    token_age_at_buy_seconds: int | None = None
    first_buy_market_cap: Decimal | None = None
    first_buy_price: Decimal | None = None
    first_buy_amount: Decimal | None = None
    first_buy_usd: Decimal | None = None
    buy_count: int = 1
    sell_count: int = 0
    total_buy: Decimal | None = None
    total_sell: Decimal | None = None
    realized_pnl: Decimal | None = None
    unrealized_pnl: Decimal | None = None
    max_unrealized_pnl: Decimal | None = None
    pnl_at_ATH: Decimal | None = None
    pnl_at_ath: Decimal | None = None
    holding_duration: int | None = None
    holding_duration_seconds: int | None = None
    sold_before_ath: bool | None = None
    survived_to_graduation: bool | None = None
    buy_rank: int | None = None
    is_bot: bool = False
    bot_probability: Decimal | None = None
    entry_delay_seconds: int | None = None
    entry_time_bucket: str | None = None
    current_balance: Decimal | None = None
    total_invested: Decimal | None = None
    total_returned: Decimal | None = None
    roi: Decimal | None = None
    current_price: Decimal | None = None
    market_cap_status: str = "UNKNOWN"
    position: PositionSnapshot | None = None
    evidence: dict[str, Any] | None = None

def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
def _decimal(value: Any) -> Decimal | None:
    try: return None if value is None else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError): return None
def _metadata(tx: dict[str, Any]) -> dict[str, Any]:
    value = tx.get("metadata_json") or tx.get("metadata") or {}
    return value if isinstance(value, dict) else {}
def _value(tx: dict[str, Any], side: str) -> Decimal | None:
    for key in (("usd_value", "amount_out_usd") if side == "BUY" else ("usd_value", "amount_in_usd")):
        value = _decimal(tx.get(key))
        if value is not None: return value
    return None
def _amount(tx: dict[str, Any], side: str) -> Decimal | None:
    for key in (("amount_out", "amount_out_token", "token_amount", "amount") if side == "BUY" else ("amount_in", "amount_in_token", "token_amount", "amount")):
        value = _decimal(tx.get(key))
        if value is not None: return value
    return None
def _price(tx: dict[str, Any]) -> Decimal | None:
    for key in ("price", "execution_price", "token_price"):
        value = _decimal(tx.get(key))
        if value is not None: return value
    return None
def _target(token: str, tx: dict[str, Any], side: str) -> bool:
    if tx.get("token_address") == token: return True
    return tx.get("token_out") == token if side == "BUY" else tx.get("token_in") == token or (tx.get("token_in") is None and tx.get("token_out") == token)
def _bucket(seconds: int) -> str:
    if seconds < 10: return "0-10 sec"
    if seconds < 30: return "10-30 sec"
    if seconds < 60: return "30-60 sec"
    if seconds < 300: return "1-5 min"
    if seconds < 900: return "5-15 min"
    return "15+ min"
def _events_for(token: str, transactions: Iterable[dict[str, Any]], created: datetime) -> list[dict[str, Any]]:
    events, seen = [], set()
    for tx in transactions:
        wallet, signature, timestamp = tx.get("wallet_address") or tx.get("trader_address"), tx.get("signature") or tx.get("tx_hash"), tx.get("timestamp")
        if not wallet or not signature or not isinstance(timestamp, datetime): continue
        side = str(tx.get("side", "")).upper()
        if side not in {"BUY", "SELL"} or not _target(token, tx, side): continue
        timestamp = _utc(timestamp)
        if timestamp < created: continue
        metadata = _metadata(tx)
        if str(metadata.get("event_type", tx.get("event_type", "SWAP"))).upper() in _NON_TRADE_EVENTS or metadata.get("removed") or tx.get("removed"): continue
        try: index = int(tx.get("event_index", 0) or 0)
        except (TypeError, ValueError): index = 0
        identity = (str(signature).lower(), index)
        if identity in seen: continue
        seen.add(identity)
        events.append({"tx": tx, "wallet": str(wallet), "timestamp": timestamp, "side": side, "usd": _value(tx, side), "amount": _amount(tx, side), "price": _price(tx), "metadata": metadata})
    return sorted(events, key=lambda e: (e["timestamp"], e["tx"].get("block_number") or 0, str(e["tx"].get("signature") or e["tx"].get("tx_hash"))))

def calculate_position(events: Iterable[dict[str, Any]]) -> PositionSnapshot:
    lots, bought, sold, invested, returned, realized = [], Decimal(0), Decimal(0), Decimal(0), Decimal(0), Decimal(0)
    realized_known, current_price, current_mc, last_ts, last_block, reorg = True, None, None, None, None, False
    for event in sorted(events, key=lambda e: (e["timestamp"], str(e["tx"].get("signature", "")))):
        tx, side, qty, usd = event["tx"], event["side"], event["amount"], event["usd"]
        last_ts, last_block, reorg = event["timestamp"], tx.get("block_number"), reorg or bool(tx.get("reorg_corrected") or event["metadata"].get("reorg_corrected"))
        if event["price"] is not None: current_price = event["price"]
        if _decimal(tx.get("market_cap")) is not None: current_mc = _decimal(tx.get("market_cap"))
        if side == "BUY":
            if qty is not None: lots.append([qty, usd or Decimal(0)]); bought += qty
            if usd is not None: invested += usd
            continue
        if qty is not None: sold += qty
        if usd is not None: returned += usd
        if qty is None or usd is None: realized_known = False; continue
        remaining, cost = qty, Decimal(0)
        while remaining > 0 and lots:
            lot_qty, lot_cost = lots[0]; matched = min(remaining, lot_qty)
            cost += lot_cost * matched / lot_qty if lot_qty else Decimal(0); remaining -= matched
            lots[0] = [lot_qty - matched, lot_cost * (lot_qty - matched) / lot_qty] if matched < lot_qty else [Decimal(0), Decimal(0)]
            if lots[0][0] == 0: lots.pop(0)
        if remaining > 0: realized_known = False
        realized += usd - cost
    balance, cost_remaining = sum((x[0] for x in lots), Decimal(0)), sum((x[1] for x in lots), Decimal(0))
    unrealized = balance * current_price - cost_remaining if current_price is not None else None
    total_value = returned + balance * current_price if current_price is not None else (returned if balance == 0 and returned else None)
    roi = (total_value - invested) / invested if total_value is not None and invested else None
    return PositionSnapshot(bought, sold, balance, invested or None, returned or None, realized if realized_known else None, unrealized, roi, current_price, current_mc, last_ts, last_block, reorg)

def identify_early_buyers(token_address: str, transactions: Iterable[dict[str, Any]], *, token_created_at: datetime, config: EarlyBuyerConfig | None = None, token_created_block: int | None = None) -> list[EarlyBuyer]:
    active, created = config or EarlyBuyerConfig(), _utc(token_created_at)
    if active.top_n <= 0 or active.max_entry_hours <= 0: return []
    events, deadline = _events_for(token_address, transactions, created), created + timedelta(hours=active.max_entry_hours)
    by_wallet: dict[str, list[dict[str, Any]]] = {}
    for event in events: by_wallet.setdefault(event["wallet"], []).append(event)
    firsts = []
    for wallet, wallet_events in by_wallet.items():
        buys = [e for e in wallet_events if e["side"] == "BUY" and e["timestamp"] <= deadline]
        if not buys: continue
        first, age = buys[0], int((buys[0]["timestamp"] - created).total_seconds())
        if active.window_type == "minutes" and age > active.effective_window_value * 60: continue
        if active.window_type == "blocks" and token_created_block is not None and (first["tx"].get("block_number") or 0) > token_created_block + active.effective_window_value: continue
        firsts.append((wallet, first, wallet_events))
    firsts.sort(key=lambda x: (x[1]["timestamp"], x[1]["tx"].get("block_number") or 0, x[0]))
    if active.window_type == "buyers": firsts = firsts[:active.effective_window_value]
    result = []
    for rank, (wallet, first, wallet_events) in enumerate(firsts, 1):
        pos, age, mc = calculate_position(wallet_events), int((first["timestamp"] - created).total_seconds()), _decimal(first["tx"].get("market_cap"))
        sold_before_ath = None
        survived_to_graduation = None
        for event in wallet_events:
            tx, metadata = event["tx"], event["metadata"]
            if event["side"] == "SELL" and (metadata.get("ath_at") or tx.get("ath_at")):
                try:
                    ath = _utc(datetime.fromisoformat(str(metadata.get("ath_at") or tx.get("ath_at"))))
                    sold_before_ath = event["timestamp"] < ath
                except ValueError:
                    pass
            if tx.get("graduated") is True or metadata.get("graduated") is True:
                survived_to_graduation = True
        result.append(EarlyBuyer(wallet_address=wallet, first_buy_time=first["timestamp"], first_buy_block=first["tx"].get("block_number"), total_buy_usd=sum((e["usd"] or Decimal(0) for e in wallet_events if e["side"] == "BUY" and e["timestamp"] <= deadline), Decimal(0)), entry_market_cap=mc, tx_signature=str(first["tx"].get("signature") or first["tx"].get("tx_hash")), token_address=token_address, token_age_at_buy=age, token_age_at_buy_seconds=age, first_buy_market_cap=mc, first_buy_price=first["price"], first_buy_amount=first["amount"], first_buy_usd=first["usd"], buy_count=sum(1 for e in wallet_events if e["side"] == "BUY"), sell_count=sum(1 for e in wallet_events if e["side"] == "SELL"), total_buy=pos.total_invested, total_sell=pos.total_returned, realized_pnl=pos.realized_pnl, unrealized_pnl=pos.unrealized_pnl, buy_rank=rank, entry_delay_seconds=age, entry_time_bucket=_bucket(age), current_balance=pos.current_balance, total_invested=pos.total_invested, total_returned=pos.total_returned, roi=pos.roi, current_price=pos.current_price, market_cap_status="KNOWN" if mc is not None else "UNKNOWN", position=pos, sold_before_ath=sold_before_ath, survived_to_graduation=survived_to_graduation, holding_duration=int((pos.last_trade_timestamp - first["timestamp"]).total_seconds()) if pos.last_trade_timestamp else 0, holding_duration_seconds=int((pos.last_trade_timestamp - first["timestamp"]).total_seconds()) if pos.last_trade_timestamp else 0, is_bot=bool(first["tx"].get("is_bot", False)), bot_probability=_decimal(first["tx"].get("bot_probability")), evidence={"observed_event_count": len(wallet_events)}))
    return result

__all__ = ["EarlyBuyer", "EarlyBuyerConfig", "PositionSnapshot", "calculate_position", "identify_early_buyers"]

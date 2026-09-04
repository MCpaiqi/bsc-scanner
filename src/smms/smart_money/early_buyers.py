from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

_NON_TRADE_EVENTS = {"TRANSFER", "LP", "AIRDROP", "INTERNAL_ROUTING", "UNKNOWN"}


@dataclass(frozen=True)
class EarlyBuyerConfig:
    top_n: int = 20
    max_entry_hours: int = 24


@dataclass(frozen=True)
class EarlyBuyer:
    wallet_address: str
    first_buy_time: datetime
    total_buy_usd: Decimal = Decimal(0)
    entry_market_cap: Decimal | None = None
    tx_signature: str = ""
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
    evidence: dict[str, Any] | None = None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _metadata(tx: dict[str, Any]) -> dict[str, Any]:
    value = tx.get("metadata_json") or tx.get("metadata") or {}
    return value if isinstance(value, dict) else {}


def _usd_value(tx: dict[str, Any], side: str) -> Decimal | None:
    keys = ("usd_value", "amount_out_usd") if side == "BUY" else ("usd_value", "amount_in_usd")
    for key in keys:
        value = _decimal(tx.get(key))
        if value is not None:
            return value
    return None


def _token_amount(tx: dict[str, Any], side: str) -> Decimal | None:
    keys = (
        ("amount_out", "amount_out_token", "token_amount", "amount")
        if side == "BUY"
        else ("amount_in", "amount_in_token", "token_amount", "amount")
    )
    for key in keys:
        value = _decimal(tx.get(key))
        if value is not None:
            return value
    return None


def _price(tx: dict[str, Any]) -> Decimal | None:
    for key in ("price", "execution_price", "token_price"):
        value = _decimal(tx.get(key))
        if value is not None:
            return value
    return None


def _target_event(token_address: str, tx: dict[str, Any], side: str) -> bool:
    if tx.get("token_address") == token_address:
        return True
    if side == "BUY":
        return tx.get("token_out") == token_address
    return tx.get("token_in") == token_address or (
        tx.get("token_in") is None and tx.get("token_out") == token_address
    )


def _known_bool(tx: dict[str, Any], metadata: dict[str, Any], *keys: str) -> bool | None:
    for key in keys:
        for source in (tx, metadata):
            value = source.get(key)
            if isinstance(value, bool):
                return value
    return None


def _ath_timestamp(tx: dict[str, Any], metadata: dict[str, Any]) -> datetime | None:
    for source in (tx, metadata):
        value = source.get("ath_at") or source.get("token_ath_at")
        if isinstance(value, datetime):
            return _utc(value)
        if value is not None:
            try:
                return datetime.fromtimestamp(float(value), tz=UTC)
            except (TypeError, ValueError, OverflowError):
                try:
                    return _utc(datetime.fromisoformat(str(value)))
                except ValueError:
                    continue
    return None


def _activity_metrics(events: list[dict[str, Any]], first_buy: dict[str, Any]) -> dict[str, Any]:
    """Aggregate only facts present in the observed event stream."""

    events = [event for event in events if event["timestamp"] >= first_buy["timestamp"]]
    buys = [event for event in events if event["side"] == "BUY"]
    sells = [event for event in events if event["side"] == "SELL"]
    buy_usd = [event["usd"] for event in buys if event["usd"] is not None]
    sell_usd = [event["usd"] for event in sells if event["usd"] is not None]
    total_buy = sum(buy_usd, Decimal(0)) if buy_usd else None
    total_sell = sum(sell_usd, Decimal(0)) if sell_usd else None

    remaining_qty = Decimal(0)
    remaining_cost = Decimal(0)
    realized = Decimal(0)
    realized_known = False
    max_unrealized: Decimal | None = None
    latest_unrealized: Decimal | None = None
    ath_pnl: Decimal | None = None
    explicit_ath_pnl: Decimal | None = None
    for event in events:
        quantity = event["amount"]
        usd = event["usd"]
        if event["side"] == "BUY":
            if quantity is not None:
                remaining_qty += quantity
            if usd is not None:
                remaining_cost += usd
        else:
            explicit = _decimal(event["tx"].get("realized_pnl") or event["tx"].get("pnl"))
            if explicit is not None:
                realized += explicit
                realized_known = True
            elif quantity is not None and usd is not None and remaining_qty > 0:
                matched = min(quantity, remaining_qty)
                cost = remaining_cost * matched / remaining_qty
                realized += usd * matched / quantity - cost
                realized_known = True
            if quantity is not None and remaining_qty > 0:
                matched = min(quantity, remaining_qty)
                before_qty = remaining_qty
                remaining_qty -= matched
                remaining_cost -= remaining_cost * matched / before_qty

        mark = event["price"]
        if mark is not None and remaining_qty >= 0:
            latest_unrealized = remaining_qty * mark - remaining_cost
            max_unrealized = (
                latest_unrealized
                if max_unrealized is None
                else max(max_unrealized, latest_unrealized)
            )
        explicit = _decimal(
            event["tx"].get("pnl_at_ATH") or event["tx"].get("pnl_at_ath")
        )
        if explicit is not None:
            explicit_ath_pnl = explicit
        is_ath = bool(event["tx"].get("is_ath") or event["metadata"].get("is_ath"))
        ath_at = _ath_timestamp(event["tx"], event["metadata"])
        if is_ath or (ath_at is not None and event["timestamp"] == ath_at):
            ath_pnl = latest_unrealized

    graduated: bool | None = None
    for event in events:
        graduated = _known_bool(
            event["tx"],
            event["metadata"],
            "survived_to_graduation",
            "graduated",
        )
        if graduated is not None:
            break
        lifecycle = str(
            event["tx"].get("lifecycle_event") or event["metadata"].get("lifecycle_event") or ""
        ).upper()
        if lifecycle in {"GRADUATED", "GRADUATION"}:
            graduated = True
            break

    sold_before_ath: bool | None = None
    for event in events:
        explicit = _known_bool(event["tx"], event["metadata"], "sold_before_ath")
        if explicit is not None:
            sold_before_ath = explicit
            break
        if event["side"] == "SELL":
            ath_at = _ath_timestamp(event["tx"], event["metadata"])
            if ath_at is not None:
                sold_before_ath = event["timestamp"] < ath_at
                break

    end_time = max(event["timestamp"] for event in events) if events else first_buy["timestamp"]
    holding_duration = max(0, int((end_time - first_buy["timestamp"]).total_seconds()))
    return {
        "buy_count": len(buys),
        "sell_count": len(sells),
        "total_buy": total_buy,
        "total_sell": total_sell,
        "realized_pnl": realized if realized_known else None,
        "unrealized_pnl": latest_unrealized,
        "max_unrealized_pnl": max_unrealized,
        "pnl_at_ATH": explicit_ath_pnl if explicit_ath_pnl is not None else ath_pnl,
        "holding_duration": holding_duration,
        "sold_before_ath": sold_before_ath,
        "survived_to_graduation": graduated,
        "evidence": {**(first_buy["metadata"] or {}), "observed_event_count": len(events)},
    }


def identify_early_buyers(
    token_address: str,
    transactions: Iterable[dict[str, Any]],
    *,
    token_created_at: datetime,
    config: EarlyBuyerConfig | None = None,
) -> list[EarlyBuyer]:
    """Identify early buyers and summarize their observed token activity.

    Eligibility and ranking use only BUY events in the configured post-creation
    window. Later events are used only to summarize behavior already observed by
    the caller; absent future facts remain ``None``.
    """

    active = config or EarlyBuyerConfig()
    if active.top_n <= 0 or active.max_entry_hours <= 0:
        return []
    created = _utc(token_created_at)
    deadline = created + timedelta(hours=active.max_entry_hours)
    by_wallet: dict[str, list[dict[str, Any]]] = {}
    seen_signatures: set[tuple[str, str, int, str]] = set()
    for tx in transactions:
        wallet = tx.get("wallet_address") or tx.get("trader_address")
        signature = tx.get("signature") or tx.get("tx_hash")
        timestamp = tx.get("timestamp")
        if not wallet or not signature or not isinstance(timestamp, datetime):
            continue
        timestamp = _utc(timestamp)
        if timestamp < created:
            continue
        side = str(tx.get("side", "")).upper()
        if side not in {"BUY", "SELL"} or not _target_event(token_address, tx, side):
            continue
        metadata = _metadata(tx)
        event_type = str(metadata.get("event_type", tx.get("event_type", "SWAP"))).upper()
        if event_type in _NON_TRADE_EVENTS:
            continue
        try:
            event_index = int(tx.get("event_index", 0) or 0)
        except (TypeError, ValueError):
            event_index = 0
        identity = (str(wallet), str(signature), event_index, side)
        if identity in seen_signatures:
            continue
        seen_signatures.add(identity)
        by_wallet.setdefault(str(wallet), []).append(
            {
                "tx": tx,
                "metadata": metadata,
                "timestamp": timestamp,
                "side": side,
                "usd": _usd_value(tx, side),
                "amount": _token_amount(tx, side),
                "price": _price(tx),
            }
        )

    candidates: list[EarlyBuyer] = []
    for wallet, events in by_wallet.items():
        events.sort(key=lambda event: (event["timestamp"], str(event["tx"].get("signature", ""))))
        early_buys = [
            event
            for event in events
            if event["side"] == "BUY" and event["timestamp"] <= deadline
        ]
        if not early_buys:
            continue
        first_buy = early_buys[0]
        early_buy_usd = sum(
            (event["usd"] or Decimal(0) for event in early_buys), Decimal(0)
        )
        metrics = _activity_metrics(events, first_buy)
        first_tx = first_buy["tx"]
        age = int((first_buy["timestamp"] - created).total_seconds())
        candidates.append(
            EarlyBuyer(
                wallet_address=wallet,
                first_buy_time=first_buy["timestamp"],
                total_buy_usd=early_buy_usd,
                entry_market_cap=first_tx.get("market_cap"),
                tx_signature=str(first_tx.get("signature") or first_tx.get("tx_hash")),
                token_address=token_address,
                token_age_at_buy=age,
                token_age_at_buy_seconds=age,
                first_buy_market_cap=first_tx.get("market_cap"),
                first_buy_price=first_buy["price"],
                first_buy_amount=first_buy["amount"],
                first_buy_usd=first_buy["usd"],
                buy_count=metrics["buy_count"],
                sell_count=metrics["sell_count"],
                total_buy=metrics["total_buy"],
                total_sell=metrics["total_sell"],
                realized_pnl=metrics["realized_pnl"],
                unrealized_pnl=metrics["unrealized_pnl"],
                max_unrealized_pnl=metrics["max_unrealized_pnl"],
                pnl_at_ATH=metrics["pnl_at_ATH"],
                pnl_at_ath=metrics["pnl_at_ATH"],
                holding_duration=metrics["holding_duration"],
                holding_duration_seconds=metrics["holding_duration"],
                sold_before_ath=metrics["sold_before_ath"],
                survived_to_graduation=metrics["survived_to_graduation"],
                is_bot=bool(first_tx.get("is_bot", False)),
                bot_probability=first_tx.get("bot_probability"),
                evidence=metrics["evidence"],
            )
        )
    ranked = sorted(
        candidates,
        key=lambda buyer: (buyer.first_buy_time, -buyer.total_buy_usd, buyer.wallet_address),
    )[: active.top_n]
    return [replace(buyer, buy_rank=index) for index, buyer in enumerate(ranked, start=1)]

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any


@dataclass(frozen=True)
class EarlyBuyerConfig:
    top_n: int = 20
    max_entry_hours: int = 24


@dataclass(frozen=True)
class EarlyBuyer:
    wallet_address: str
    first_buy_time: datetime
    total_buy_usd: Decimal
    entry_market_cap: Decimal | None
    tx_signature: str
    token_address: str | None = None
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
    pnl_at_ath: Decimal | None = None
    holding_duration_seconds: int | None = None
    sold_before_ath: bool | None = None
    survived_to_graduation: bool | None = None
    buy_rank: int | None = None
    is_bot: bool = False
    bot_probability: Decimal | None = None
    evidence: dict[str, Any] | None = None


def identify_early_buyers(
    token_address: str,
    transactions: Iterable[dict[str, Any]],
    *,
    token_created_at: datetime,
    config: EarlyBuyerConfig | None = None,
) -> list[EarlyBuyer]:
    """Identify unique wallets whose first authoritative action was a target-token BUY."""

    active = config or EarlyBuyerConfig()
    if active.top_n <= 0 or active.max_entry_hours <= 0:
        return []
    created = token_created_at.astimezone(UTC)
    deadline = created + timedelta(hours=active.max_entry_hours)
    by_wallet: dict[str, EarlyBuyer] = {}
    seen_signatures: set[tuple[str, str]] = set()
    for tx in transactions:
        wallet = tx.get("wallet_address")
        signature = tx.get("signature")
        token_out = tx.get("token_out")
        timestamp = tx.get("timestamp")
        if not wallet or not signature or token_out != token_address or not isinstance(timestamp, datetime):
            continue
        timestamp = timestamp.astimezone(UTC)
        if not created <= timestamp <= deadline or tx.get("side") != "BUY":
            continue
        identity = (wallet, signature)
        if identity in seen_signatures:
            continue
        seen_signatures.add(identity)
        metadata = tx.get("metadata_json") or tx.get("metadata") or {}
        event_type = str(metadata.get("event_type", "")).upper()
        if event_type in {"TRANSFER", "LP", "AIRDROP", "INTERNAL_ROUTING", "UNKNOWN"}:
            continue
        amount = tx.get("usd_value") or tx.get("amount_out_usd") or 0
        try:
            amount_decimal = Decimal(str(amount))
        except (InvalidOperation, TypeError, ValueError):
            amount_decimal = Decimal(0)
        candidate = EarlyBuyer(
            wallet_address=wallet,
            first_buy_time=timestamp,
            total_buy_usd=amount_decimal,
            entry_market_cap=tx.get("market_cap"),
            tx_signature=signature,
            token_address=token_address,
            token_age_at_buy_seconds=int((timestamp - created).total_seconds()),
            first_buy_market_cap=tx.get("market_cap"),
            first_buy_price=tx.get("price"),
            first_buy_amount=tx.get("amount_out"),
            first_buy_usd=amount_decimal,
            total_buy=amount_decimal,
            is_bot=bool(tx.get("is_bot", False)),
            bot_probability=tx.get("bot_probability"),
            evidence=metadata,
        )
        existing = by_wallet.get(wallet)
        if existing is None:
            by_wallet[wallet] = candidate
            continue
        total_buy_usd = existing.total_buy_usd + candidate.total_buy_usd
        buy_count = existing.buy_count + candidate.buy_count
        if (candidate.first_buy_time, -candidate.total_buy_usd) < (
            existing.first_buy_time,
            -existing.total_buy_usd,
        ):
            by_wallet[wallet] = replace(
                candidate, total_buy_usd=total_buy_usd, total_buy=total_buy_usd, buy_count=buy_count
            )
        else:
            by_wallet[wallet] = replace(
                existing, total_buy_usd=total_buy_usd, total_buy=total_buy_usd, buy_count=buy_count
            )
    ranked = sorted(
        by_wallet.values(),
        key=lambda buyer: (buyer.first_buy_time, -buyer.total_buy_usd, buyer.wallet_address),
    )[: active.top_n]
    return [replace(buyer, buy_rank=index) for index, buyer in enumerate(ranked, start=1)]

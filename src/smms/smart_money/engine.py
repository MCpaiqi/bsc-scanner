"""Persistence facade for replaying normalized trade events."""
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from smms.db.models import EarlyBuyer as EarlyBuyerRow
from smms.db.models import WalletTokenPosition
from smms.db.repositories import EarlyBuyerRepository, WalletTokenPositionRepository

from .early_buyers import EarlyBuyerConfig, calculate_position, identify_early_buyers


class EarlyBuyerEngine:
    """Rebuilds one token's early buyers and positions idempotently."""

    def __init__(self, session: Session, *, chain: str = "bsc") -> None:
        self.session = session
        self.chain = chain

    def replay(
        self,
        token_address: str,
        transactions: Iterable[dict[str, Any]],
        *,
        token_created_at: datetime,
        token_created_block: int | None = None,
        config: EarlyBuyerConfig | None = None,
    ) -> list[Any]:
        txs = list(transactions)
        buyers = identify_early_buyers(token_address, txs, token_created_at=token_created_at, token_created_block=token_created_block, config=config)
        buyer_repo, position_repo = EarlyBuyerRepository(self.session), WalletTokenPositionRepository(self.session)
        for buyer in buyers:
            position = buyer.position or calculate_position([])
            buyer_repo.upsert(EarlyBuyerRow(chain=self.chain, wallet_address=buyer.wallet_address, token_address=token_address, first_buy_tx=buyer.tx_signature, first_buy_block=buyer.first_buy_block, first_buy_timestamp=buyer.first_buy_time, first_buy_price=buyer.first_buy_price, first_buy_usd=buyer.first_buy_usd, first_buy_market_cap=buyer.first_buy_market_cap, market_cap_status=buyer.market_cap_status, buy_rank=buyer.buy_rank or 0, buy_count=buyer.buy_count, total_buy_usd=buyer.total_buy_usd, entry_delay_seconds=buyer.entry_delay_seconds, entry_time_bucket=buyer.entry_time_bucket, window_type=(config or EarlyBuyerConfig()).window_type, window_value=(config or EarlyBuyerConfig()).effective_window_value, metadata_json=buyer.evidence or {}))
            position_repo.upsert(WalletTokenPosition(chain=self.chain, wallet_address=buyer.wallet_address, token_address=token_address, cumulative_bought=position.cumulative_bought, cumulative_sold=position.cumulative_sold, current_balance=position.current_balance, total_invested=position.total_invested, total_returned=position.total_returned, realized_pnl=position.realized_pnl, unrealized_pnl=position.unrealized_pnl, roi=position.roi, current_price=position.current_price, current_market_cap=position.current_market_cap, last_trade_timestamp=position.last_trade_timestamp, last_trade_block=position.last_trade_block, is_reorg_corrected=position.reorg_corrected, metadata_json={"source": "normalized_trades"}))
        return buyers


__all__ = ["EarlyBuyerEngine"]

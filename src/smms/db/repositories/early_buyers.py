from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from smms.db.models import EarlyBuyer, WalletTokenPosition


class EarlyBuyerRepository:
    def __init__(self, session: Session): self.session = session
    def upsert(self, record: EarlyBuyer) -> EarlyBuyer:
        existing = self.session.scalar(select(EarlyBuyer).where(EarlyBuyer.chain == record.chain, EarlyBuyer.wallet_address == record.wallet_address, EarlyBuyer.token_address == record.token_address))
        if existing is None:
            try:
                with self.session.begin_nested(): self.session.add(record); self.session.flush()
                return record
            except IntegrityError:
                existing = self.session.scalar(select(EarlyBuyer).where(EarlyBuyer.chain == record.chain, EarlyBuyer.wallet_address == record.wallet_address, EarlyBuyer.token_address == record.token_address))
                if existing is None: raise
        for name in ("first_buy_tx", "first_buy_block", "first_buy_timestamp", "first_buy_price", "first_buy_usd", "first_buy_market_cap", "market_cap_status", "buy_rank", "buy_count", "total_buy_usd", "entry_delay_seconds", "entry_time_bucket", "window_type", "window_value", "metadata_json"):
            setattr(existing, name, getattr(record, name))
        return existing


class WalletTokenPositionRepository:
    def __init__(self, session: Session): self.session = session
    def upsert(self, record: WalletTokenPosition) -> WalletTokenPosition:
        existing = self.session.scalar(select(WalletTokenPosition).where(WalletTokenPosition.chain == record.chain, WalletTokenPosition.wallet_address == record.wallet_address, WalletTokenPosition.token_address == record.token_address))
        if existing is None:
            try:
                with self.session.begin_nested(): self.session.add(record); self.session.flush()
                return record
            except IntegrityError:
                existing = self.session.scalar(select(WalletTokenPosition).where(WalletTokenPosition.chain == record.chain, WalletTokenPosition.wallet_address == record.wallet_address, WalletTokenPosition.token_address == record.token_address))
                if existing is None: raise
        for name in ("cumulative_bought", "cumulative_sold", "current_balance", "total_invested", "total_returned", "realized_pnl", "unrealized_pnl", "roi", "current_price", "current_market_cap", "last_trade_timestamp", "last_trade_block", "is_reorg_corrected", "metadata_json"):
            setattr(existing, name, getattr(record, name))
        return existing

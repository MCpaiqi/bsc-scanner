"""Immutable early-buyer and position publication from canonical replay inputs."""

from dataclasses import asdict
from datetime import datetime

from sqlalchemy import select

from smms.db.models import EarlyBuyer as EarlyBuyerRow
from smms.db.models import WalletTokenPosition
from smms.evaluation import utc
from smms.research.lineage import digest, json_value
from smms.research.publication import publish_snapshot

from .early_buyers import EarlyBuyerConfig, _events_for, calculate_position
from .replay import REPLAY_VERSION, ReplayInputs, load_replay_inputs


class EarlyBuyerEngine:
    def __init__(self, session, *, chain="bsc"):
        self.session, self.chain = session, chain

    def refresh(self, token_address, *, as_of, config=None, recipe_hash=None, reconstruction_policy=None):
        inputs = load_replay_inputs(self.session, token_address, as_of=as_of, chain=self.chain)
        return self._publish(inputs, config or EarlyBuyerConfig(), recipe_hash=recipe_hash,
                             reconstruction_policy=reconstruction_policy)

    def replay(self, token_address, transactions, *, token_created_at, token_created_block=None, config=None):
        """Legacy dictionary imports remain unverified; use refresh for trusted reads."""
        txs = tuple(transactions)
        cutoff = max((utc(tx["timestamp"]) for tx in txs), default=utc(token_created_at))
        inputs = ReplayInputs(self.chain, token_address, cutoff, 0,
                              json_value({"creation_timestamp": token_created_at, "creation_block": token_created_block}),
                              txs, tuple({"table": "unverified_import", "values": json_value(tx)} for tx in txs))
        return self._publish(inputs, config or EarlyBuyerConfig(), reconstruction_policy="unverified-import-v1")

    def _publish(self, inputs, config, *, recipe_hash=None, reconstruction_policy=None):
        buyers = inputs.buyers(config=config)
        recipe_hash = recipe_hash or digest({"recipe": REPLAY_VERSION, "config": asdict(config)})
        source = inputs.input_manifest(config=config)
        if reconstruction_policy:
            source["parameters"]["reconstruction_policy"] = reconstruction_policy
            source["digest"] = digest({key: value for key, value in source.items() if key != "digest"})
        common = {"chain": self.chain, "token_address": inputs.token, "as_of": inputs.as_of,
                  "recipe_hash": recipe_hash, "source_epoch": inputs.source_epoch, "input_manifest": source}
        buyer_rows = {}
        for buyer in buyers:
            buyer_rows[buyer.wallet_address] = EarlyBuyerRow(
                **common, wallet_address=buyer.wallet_address, first_buy_tx=buyer.tx_signature,
                first_buy_block=buyer.first_buy_block, first_buy_timestamp=buyer.first_buy_time,
                first_buy_price=buyer.first_buy_price, first_buy_usd=buyer.first_buy_usd,
                first_buy_market_cap=buyer.first_buy_market_cap, market_cap_status=buyer.market_cap_status,
                buy_rank=buyer.buy_rank, buy_count=buyer.buy_count, total_buy_usd=buyer.total_buy_usd,
                entry_delay_seconds=buyer.entry_delay_seconds, entry_time_bucket=buyer.entry_time_bucket,
                window_type=config.window_type, window_value=config.effective_window_value,
                metadata_json=buyer.evidence or {},
            )
        position_rows = {}
        if inputs.pair:
            events = _events_for(inputs.token, inputs.transactions,
                                 datetime.fromisoformat(inputs.pair["creation_timestamp"]))
            wallets = {}
            for event in events:
                wallets.setdefault(event["wallet"], []).append(event)
            for wallet, wallet_events in wallets.items():
                p = calculate_position(wallet_events)
                position_rows[wallet] = WalletTokenPosition(
                    **common, wallet_address=wallet, cumulative_bought=p.cumulative_bought,
                    cumulative_sold=p.cumulative_sold, current_balance=p.current_balance,
                    total_invested=p.total_invested, total_returned=p.total_returned,
                    realized_pnl=p.realized_pnl, unrealized_pnl=p.unrealized_pnl, roi=p.roi,
                    current_price=p.current_price, current_market_cap=p.current_market_cap,
                    last_trade_timestamp=p.last_trade_timestamp, last_trade_block=p.last_trade_block,
                    is_reorg_corrected=p.reorg_corrected, metadata_json={"source": REPLAY_VERSION},
                )
        for model, records in ((EarlyBuyerRow, buyer_rows), (WalletTokenPosition, position_rows)):
            history = self.session.scalars(select(model).where(
                model.chain == self.chain, model.token_address == inputs.token, model.as_of == inputs.as_of,
                model.recipe_hash == recipe_hash,
            ).order_by(model.research_revision.desc()).execution_options(include_invalid_snapshots=True)).all()
            withdrawn = set()
            for old in history:
                if old.wallet_address in records or old.wallet_address in withdrawn:
                    continue
                withdrawn.add(old.wallet_address)
                # Old payload is an unavailable audit record, never a zero observation.
                values = {c.name: getattr(old, c.name) for c in model.__table__.columns
                          if c.name not in {"id", "research_revision", "previous_snapshot_id", "snapshot_status"}}
                values.update(common, snapshot_status="UNAVAILABLE")
                records[old.wallet_address] = model(**values)
            for record in records.values():
                publish_snapshot(self.session, record)
        return buyers


__all__ = ["EarlyBuyerEngine"]

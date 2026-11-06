"""Raw BSC event to canonical market-event pipeline."""

import logging
from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from smms.chains.bsc.active_pairs import ActivePairRegistry
from smms.chains.bsc.transactions import TransactionContextResolver
from smms.chains.constants import BSC_CHAIN_ID, normalize_address
from smms.db.models import MarketEvent, Pair, Token, TradeEvent
from smms.db.repositories.async_repositories import AsyncTokenRepository

from .models import MarketEventRecord, PairRecord, TradeSide
from .protocols import ProtocolRegistry

logger = logging.getLogger(__name__)

_TOKEN_SYMBOL_MAX_LENGTH = 32
_TOKEN_NAME_MAX_LENGTH = 128


def _bounded_token_text(value: Any, max_length: int) -> str | None:
    """Keep DB text columns bounded while retaining the raw value in metadata."""
    if value is None:
        return None
    return str(value)[:max_length]


class BSCMarketPipeline:
    """Decode raw logs and persist discovery/events idempotently."""

    def __init__(self, session: AsyncSession, *, registry: ProtocolRegistry | None = None, metadata_provider: Any = None, transaction_resolver: TransactionContextResolver | None = None, active_pair_registry: ActivePairRegistry | None = None, defer_radar_projection: bool = False, deferred_radar_rows: list[Any] | None = None, deferred_trades: list[Any] | None = None, allow_indexed_trader_fallback: bool = False) -> None:
        self.session = session
        self.registry = registry or ProtocolRegistry()
        self.metadata_provider = metadata_provider
        settings = getattr(metadata_provider, "settings", None)
        self.transaction_resolver = transaction_resolver or TransactionContextResolver(
            metadata_provider,
            ttl_seconds=getattr(settings, "transaction_context_cache_ttl_seconds", 300),
            max_entries=getattr(settings, "transaction_context_cache_max_entries", 10_000),
        )
        self.active_pair_registry = active_pair_registry
        self.defer_radar_projection = defer_radar_projection
        self.deferred_radar_rows = deferred_radar_rows
        self.deferred_trades = deferred_trades
        self.allow_indexed_trader_fallback = allow_indexed_trader_fallback
        self._metadata_cache: dict[str, dict[str, Any]] = {}

    async def process_raw_event(self, raw: Any) -> tuple[Pair | None, MarketEvent | None]:
        from smms.chains.base import ChainLog
        if raw.removed or raw.confirmation_status == "orphaned":
            from smms.radar.hooks import revalidate

            await self._invalidate_event(raw)
            await revalidate(self.session, raw)
            return None, None
        log = ChainLog(
            address=raw.contract_address,
            topics=tuple(raw.topics or ()),
            data=raw.data,
            block_number=raw.block_number,
            transaction_hash=raw.transaction_hash,
            log_index=raw.log_index,
            timestamp=raw.timestamp,
            block_hash=raw.block_hash,
            removed=raw.removed,
            confirmation_status=raw.confirmation_status,
            raw=raw.raw_payload_json or {},
        )
        adapter = self.registry.detect(log)
        if adapter is None:
            from smms.observability import metrics
            metrics.increment("raw_logs_received")
            metrics.increment("logs_unknown")
            return None, await self._persist_unknown(log, "unknown_protocol")
        from smms.observability import metrics
        metrics.increment("raw_logs_received")
        metrics.increment("logs_matched")
        pair_record, event_record = adapter.normalize(log)
        if pair_record is not None:
            metrics.increment("pair_created_decoded")
        if event_record is not None and event_record.event_type == "TRADE":
            metrics.increment("swaps_decoded")
        pair = await self._persist_pair(pair_record) if pair_record else None
        if pair_record is not None and self.active_pair_registry is not None:
            adapter_config = getattr(adapter, "config", None)
            allowed_quotes = {
                normalize_address(value, chain="bsc")
                for value in getattr(adapter_config, "quote_assets", ())
                if value
            }
            quote = normalize_address(pair_record.quote_token, chain="bsc") if pair_record.quote_token else None
            if quote is not None and (not allowed_quotes or quote in allowed_quotes):
                self.active_pair_registry.add(pair_record)
        if pair_record is not None:
            await self._persist_token(pair_record.token0, raw)
            await self._persist_token(pair_record.token1, raw)
        if event_record is not None and pair is None and event_record.pair:
            pair = await self.session.scalar(
                select(Pair).where(
                    Pair.chain_id == event_record.chain_id,
                    Pair.address == event_record.pair,
                )
            )
        if event_record is not None and pair is not None:
            # Swap logs do not carry PairCreated data. Refresh only missing
            # token metadata so raw amounts can be converted into TradeEvent
            # units without overwriting durable creation facts.
            await self._persist_token(pair.token0_address, raw)
            await self._persist_token(pair.token1_address, raw)
        if event_record is not None and pair is not None and event_record.token is None:
            event_record = self._attach_pair_token(event_record, pair)
        if event_record is None and pair_record is not None and pair is not None:
            # PairCreated is a canonical discovery projection as well as the
            # source of pair activation. Keeping it in MarketEvent makes the
            # raw -> market projection boundary complete and replayable.
            token_address = (
                pair.token1_address
                if pair.quote_token and pair.token0_address == pair.quote_token
                else pair.token0_address
            )
            event_record = MarketEventRecord(
                event_type="PAIR_CREATED",
                chain_id=pair.chain_id,
                token=token_address,
                pair=pair.address,
                wallet=None,
                tx_hash=pair.creation_tx_hash or log.transaction_hash,
                block=pair.creation_block or log.block_number,
                timestamp=pair.creation_timestamp or log.timestamp or datetime.now(UTC),
                side=TradeSide.UNKNOWN,
                source="bsc:pancakeswap",
                confidence=Decimal("1.00"),
                metadata={
                    "topic0": log.topics[0] if log.topics else None,
                    "log_index": log.log_index,
                    "block_hash": log.block_hash,
                    "pair_created": True,
                },
            )
        if event_record is not None:
            if pair is not None:
                self.transaction_resolver.add_system_addresses(
                    {pair.address, pair.factory_address} if pair.factory_address else {pair.address}
                )
            transaction_context = None
            if event_record.event_type == "TRADE":
                transaction_context = await self.transaction_resolver.resolve(
                    event_record.tx_hash,
                    swap_sender=event_record.metadata.get("swap_sender"),
                    swap_recipient=event_record.metadata.get("swap_recipient"),
                )
            transaction_from = (
                (raw.raw_payload_json or {}).get("transaction_from")
                or (raw.raw_payload_json or {}).get("from")
                or (transaction_context.transaction_from if transaction_context else None)
            )
            # A resolved transaction caller is the only authoritative economic
            # trader for live swaps. Never retain indexed Swap participants when
            # the RPC lookup failed, or when the caller is a known system address.
            trader_address = event_record.wallet
            if event_record.event_type == "TRADE":
                if self.transaction_resolver.is_system_address(transaction_from):
                    trader_address = None
                elif transaction_from:
                    trader_address = transaction_from
                elif self.allow_indexed_trader_fallback:
                    # Historical coverage can persist a Swap's indexed
                    # participant without fabricating a transaction caller.
                    # The stored resolution remains explicit and low-confidence.
                    trader_address = event_record.wallet
                else:
                    trader_address = None
            event_record = MarketEventRecord(
                **{
                    **event_record.__dict__,
                    "wallet": trader_address,
                    "metadata": {
                        **event_record.metadata,
                        "log_index": log.log_index,
                        "block_hash": getattr(raw, "block_hash", None),
                        "transaction_from": transaction_from,
                        "transaction_to": transaction_context.transaction_to if transaction_context else None,
                        "transaction_input": transaction_context.input_data if transaction_context else None,
                        "trader_resolution": transaction_context.resolution if transaction_context else "UNRESOLVED",
                        "trader_confidence": transaction_context.confidence if transaction_context else "UNKNOWN",
                    },
                }
            )
        event = await self._persist_event(event_record) if event_record else None
        trade = await self._persist_trade(event_record, pair) if event_record and pair else None
        if trade is not None:
            from .snapshots import materialize_trade_snapshot
            if self.defer_radar_projection and self.deferred_trades is not None:
                self.deferred_trades.append(trade)
            else:
                await materialize_trade_snapshot(self.session, trade, as_of=event_record.timestamp)
        if pair_record is not None:
            pair.metadata_json = {**(pair.metadata_json or {}), "log_index": raw.log_index,
                                  "block_hash": getattr(raw, "block_hash", None)}
            if self.defer_radar_projection:
                if self.deferred_radar_rows is not None:
                    self.deferred_radar_rows.append(pair)
            else:
                from smms.radar.hooks import project

                await project(self.session, pair)
        if event is not None:
            event.metadata_json = {**event.metadata_json, "block_hash": getattr(raw, "block_hash", None)}
            if self.defer_radar_projection:
                if self.deferred_radar_rows is not None:
                    self.deferred_radar_rows.append(event)
            else:
                from smms.radar.hooks import project

                await project(self.session, event)
        return pair, event

    async def _persist_trade(
        self, record: MarketEventRecord, pair: Pair
    ) -> TradeEvent | None:
        if (
            record.event_type != "TRADE"
            or record.token is None
            or record.wallet is None
            or record.side not in {TradeSide.BUY, TradeSide.SELL}
        ):
            return None
        index = int(record.metadata.get("log_index", 0))
        tx_hash = str(record.tx_hash).lower()
        trade = await self.session.scalar(
            select(TradeEvent).where(
                TradeEvent.chain == "bsc",
                TradeEvent.tx_hash == tx_hash,
                TradeEvent.event_index == index,
                TradeEvent.block_hash == record.metadata.get("block_hash"),
            )
        )
        token = await self.session.scalar(
            select(Token).where(Token.chain == "bsc", Token.address == record.token)
        )
        base = await self.session.scalar(
            select(Token).where(Token.chain == "bsc", Token.address == pair.quote_token)
        ) if pair.quote_token else None
        token_decimals = token.decimals if token is not None else None
        base_decimals = base.decimals if base is not None else None
        token_amount = (
            record.token_amount / (Decimal(10) ** token_decimals)
            if record.token_amount is not None and token_decimals is not None
            else None
        )
        base_amount = (
            record.quote_amount / (Decimal(10) ** base_decimals)
            if record.quote_amount is not None and base_decimals is not None
            else None
        )
        quote_is_native = (pair.quote_token or "").lower() == "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"
        price_quote = (
            base_amount / token_amount
            if base_amount is not None and token_amount not in (None, Decimal(0))
            else None
        )
        values = {
            "chain_id": record.chain_id,
            "launchpad": None,
            "token_address": record.token,
            "pair_address": pair.address,
            "base_token_address": pair.quote_token,
            "trader_address": record.wallet,
            "block_number": record.block,
            "block_hash": record.metadata.get("block_hash"),
            # Some public RPCs omit block timestamps on log responses and
            # can rate-limit the follow-up block lookup. MarketEvent.timestamp
            # is canonical and non-null, so retain the event rather than
            # aborting the live worker when enrichment is unavailable.
            "timestamp": record.timestamp or datetime.now(UTC),
            "side": record.side.value,
            "token_amount": token_amount,
            "native_amount": base_amount if quote_is_native else None,
            "quote_amount": base_amount,
            "token_decimals": token_decimals,
            "base_decimals": base_decimals,
            "quote_decimals": base_decimals,
            "price": record.price,
            "price_quote": price_quote,
            "price_usd": record.price_usd,
            "value_quote": base_amount,
            "value_usd": record.usd_value,
            "source": record.source,
            "canonical": True,
            "removed": False,
            "swap_sender": record.metadata.get("swap_sender"),
            "swap_recipient": record.metadata.get("swap_recipient"),
            "transaction_from": record.metadata.get("transaction_from"),
            "transaction_to": record.metadata.get("transaction_to"),
            "trader_resolution": record.metadata.get("trader_resolution", "UNKNOWN"),
            "trader_confidence": record.metadata.get("trader_confidence", "UNKNOWN"),
            "metadata_json": {
                **record.metadata,
                "dex": pair.dex,
                "amount_status": "KNOWN" if token_amount is not None and base_amount is not None else "UNKNOWN",
                "amount_reason_code": None if token_amount is not None and base_amount is not None else "TOKEN_DECIMALS_UNKNOWN",
            },
        }
        if trade is None:
            trade = TradeEvent(chain="bsc", tx_hash=tx_hash, event_index=index, **values)
            self.session.add(trade)
            from smms.observability import metrics

            metrics.increment("trade_events_created")
        else:
            from smms.observability import metrics

            metrics.increment("trade_events_deduplicated")
            for key, value in values.items():
                if value is not None:
                    setattr(trade, key, value)
        await self.session.flush()
        return trade

    async def process_raw_events(self, raws: Iterable[Any]) -> list[tuple[Pair | None, MarketEvent | None]]:
        results = []
        for raw in sorted(raws, key=lambda item: (item.block_number, item.log_index)):
            results.append(await self.process_raw_event(raw))
        await self.session.flush()
        return results

    async def _persist_pair(self, record: PairRecord) -> Pair:
        pair = await self.session.scalar(
            select(Pair).where(Pair.chain_id == record.chain_id, Pair.address == record.address)
        )
        values = {
            "token0_address": record.token0,
            "token1_address": record.token1,
            "factory_address": record.factory,
            "dex": record.dex,
            "quote_token": record.quote_token,
            "creation_tx_hash": record.creation_tx_hash,
            "creation_block": record.creation_block,
            "creation_timestamp": record.creation_timestamp,
            "creation_block_hash": (
                record.creation_block_hash.lower() if record.creation_block_hash else None
            ),
            "canonical": True,
            "removed": False,
        }
        if pair is None:
            pair = Pair(chain_id=record.chain_id, address=record.address, **values)
            self.session.add(pair)
            await self.session.flush()
        else:
            for key, value in values.items():
                if value is not None:
                    setattr(pair, key, value)
        from smms.alpha.work import register_pair
        await self.session.run_sync(lambda sync: register_pair(sync, pair))
        return pair

    async def _persist_event(self, record: MarketEventRecord) -> MarketEvent:
        index = int(record.metadata.get("log_index", 0))
        block_hash = record.metadata.get("block_hash")
        tx_hash = str(record.tx_hash).lower()
        event = await self.session.scalar(
            select(MarketEvent).where(
                MarketEvent.chain_id == record.chain_id,
                MarketEvent.tx_hash == tx_hash,
                MarketEvent.event_index == index,
                MarketEvent.block_hash == block_hash,
            )
        )
        values = {
            "event_type": record.event_type,
            "token_address": record.token,
            "pair_address": record.pair,
            "wallet_address": record.wallet,
            "block_number": record.block,
            "block_hash": block_hash,
            "timestamp": record.timestamp,
            "side": record.side.value if record.side else None,
            "token_amount": record.token_amount,
            "quote_amount": record.quote_amount,
            "usd_value": record.usd_value,
            "price": record.price,
            "source": record.source,
            "confidence": record.confidence,
            "metadata_json": {**record.metadata, "log_index": index},
        }
        if event is None:
            event = MarketEvent(chain_id=record.chain_id, tx_hash=tx_hash, event_index=index, **values)
            self.session.add(event)
            await self.session.flush()
        else:
            for key, value in values.items():
                if value is not None:
                    setattr(event, key, value)
        return event

    async def _persist_token(self, address: str, raw: Any) -> Token:
        repo = AsyncTokenRepository(self.session)
        canonical = normalize_address(address, chain="bsc")
        existing = await repo.get_by_address(canonical, "bsc")
        if existing is None:
            token = await repo.upsert(
                address=canonical,
                chain="bsc",
                first_seen_at=raw.timestamp or datetime.now(UTC),
                status="NEW",
            )
        else:
            token = existing
        # Do not re-query immutable ERC-20 metadata for every later swap.
        # Missing fields remain eligible for the retryable backfill worker.
        if self.metadata_provider is not None and any(
            getattr(token, field) is None for field in ("symbol", "name", "decimals")
        ):
            from .metadata import enrich_erc20_metadata

            metadata = await enrich_erc20_metadata(
                self.metadata_provider,
                canonical,
                cache=self._metadata_cache,
            )
            for key, max_length in (
                ("symbol", _TOKEN_SYMBOL_MAX_LENGTH),
                ("name", _TOKEN_NAME_MAX_LENGTH),
            ):
                if getattr(token, key) is None and metadata.get(key) is not None:
                    setattr(token, key, _bounded_token_text(metadata[key], max_length))
            if token.decimals is None and metadata.get("decimals") is not None:
                token.decimals = metadata["decimals"]
            if metadata.get("metadata_status") is not None:
                token.metadata_json = {**(token.metadata_json or {}), **metadata}
            await self.session.flush()
        return token

    async def _persist_unknown(self, log: Any, reason: str) -> MarketEvent:
        return await self._persist_event(
            MarketEventRecord(
                event_type="UNKNOWN",
                chain_id=(
                    self.registry.adapters[0].chain_id
                    if self.registry.adapters
                    else BSC_CHAIN_ID
                ),
                token=None,
                pair=log.address,
                wallet=None,
                tx_hash=log.transaction_hash,
                block=log.block_number,
                timestamp=log.timestamp or datetime.now(UTC),
                side=TradeSide.UNKNOWN,
                source="bsc:unknown",
                confidence=0,
                metadata={
                    "reason": reason,
                    "topic0": log.topics[0] if log.topics else None,
                    "log_index": log.log_index,
                    "block_hash": log.block_hash,
                },
            )
        )

    async def _invalidate_event(self, raw: Any) -> None:
        """Mark only the matching fork's market projection as non-canonical."""
        raw_block_hash = getattr(raw, "block_hash", None)
        if not raw_block_hash:
            logger.warning("removed log missing block_hash; deferring reorg evidence")
            metadata = dict(getattr(raw, "raw_payload_json", None) or {})
            metadata["reorg_evidence"] = "UNRESOLVED_MISSING_BLOCK_HASH"
            raw.raw_payload_json = metadata
            await self.session.flush()
            return

        orphaned_pairs = (
            await self.session.scalars(
                select(Pair).where(
                    Pair.chain_id == BSC_CHAIN_ID,
                    Pair.creation_tx_hash == str(raw.transaction_hash).lower(),
                    Pair.creation_block_hash == str(raw_block_hash).lower(),
                )
            )
        ).all()
        for pair in orphaned_pairs:
            pair.canonical = False
            pair.removed = True
            if self.active_pair_registry is not None:
                self.active_pair_registry.remove(pair.address, state="ORPHANED")

        candidates = (
            await self.session.scalars(
                select(MarketEvent).where(
                    MarketEvent.chain_id == BSC_CHAIN_ID,
                    MarketEvent.tx_hash == str(raw.transaction_hash).lower(),
                    MarketEvent.event_index == int(raw.log_index),
                )
            )
        ).all()
        for event in candidates:
            if event.block_hash != str(raw_block_hash).lower():
                continue
            event.metadata_json = {
                **(event.metadata_json or {}),
                "block_hash": event.block_hash,
                "removed": True,
                "orphaned": True,
            }
        trades = (
            await self.session.scalars(
                select(TradeEvent).where(
                    TradeEvent.chain == "bsc",
                    TradeEvent.tx_hash == str(raw.transaction_hash).lower(),
                    TradeEvent.event_index == int(raw.log_index),
                    TradeEvent.block_hash == str(raw_block_hash).lower(),
                )
            )
        ).all()
        for trade in trades:
            trade.canonical = False
            trade.removed = True
            trade.metadata_json = {**(trade.metadata_json or {}), "orphaned": True}
        await self.session.flush()

    @staticmethod
    def _attach_pair_token(record: MarketEventRecord, pair: Pair) -> MarketEventRecord:
        token = pair.token0_address if pair.quote_token == pair.token1_address else pair.token1_address
        metadata = record.metadata
        amounts = metadata.get("amounts_raw", [])
        side = record.side
        token_amount = record.token_amount
        quote_amount = record.quote_amount
        if len(amounts) == 4 and pair.quote_token:
            amount0_in, amount1_in, amount0_out, amount1_out = (int(value) for value in amounts)
            token0_is_quote = normalize_address(pair.token0_address, chain="bsc") == normalize_address(pair.quote_token, chain="bsc")
            token_in = amount1_in if token0_is_quote else amount0_in
            token_out = amount1_out if token0_is_quote else amount0_out
            quote_in = amount0_in if token0_is_quote else amount1_in
            quote_out = amount0_out if token0_is_quote else amount1_out
            if token_in and quote_out and not token_out and not quote_in:
                side, token_amount, quote_amount = TradeSide.SELL, Decimal(token_in), Decimal(quote_out)
            elif token_out and quote_in and not token_in and not quote_out:
                side, token_amount, quote_amount = TradeSide.BUY, Decimal(token_out), Decimal(quote_in)
        confidence = Decimal("0.90") if side is not TradeSide.UNKNOWN else record.confidence
        participants = record.metadata
        wallet = (
            participants.get("transaction_from")
            or (participants.get("swap_recipient") if side is TradeSide.BUY else None)
            or (participants.get("swap_sender") if side is TradeSide.SELL else None)
            or record.wallet
        )
        return MarketEventRecord(
            **{
                **record.__dict__,
                "token": token,
                "side": side,
                "token_amount": token_amount,
                "quote_amount": quote_amount,
                "wallet": wallet,
                "confidence": confidence,
                "metadata": {
                    **record.metadata,
                    "trader_resolution": (
                        "transaction_from" if participants.get("transaction_from") else
                        "swap_recipient" if side is TradeSide.BUY else
                        "swap_sender" if side is TradeSide.SELL else "unresolved"
                    ),
                },
            }
        )

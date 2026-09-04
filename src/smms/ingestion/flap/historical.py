"""Historical Flap token backfill orchestration."""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from smms.chains.base import ChainLog, ChainProvider, ChainTransaction, TransactionReceipt
from smms.db.models import (
    Token,
    TokenLifecycleEvent,
    TokenStatus,
    TradeEvent,
    WalletTokenPerformance,
)
from smms.db.repositories import (
    AsyncRawChainRecordRepository,
    AsyncTokenLifecycleEventRepository,
    AsyncTokenRepository,
    AsyncTradeEventRepository,
    AsyncWalletRepository,
    AsyncWalletTokenPerformanceRepository,
)
from smms.ingestion import DataUnavailable
from smms.launchpads.base import LaunchpadTokenDiscovery, apply_lifecycle_event
from smms.launchpads.flap import FlapAdapter, FlapConfig
from smms.launchpads.flap._decoding import log_matches

from .performance import calculate_wallet_token_performance
from .quality import FlapDataQualityReport


@dataclass(frozen=True)
class FlapBackfillResult:
    token_address: str
    creator_address: str | None
    creation_block: int
    creation_tx_hash: str
    created_at: datetime
    token: Token | None
    trades: tuple[TradeEvent, ...]
    lifecycle: tuple[TokenLifecycleEvent, ...]
    performances: tuple[WalletTokenPerformance, ...]
    quality: FlapDataQualityReport


class FlapHistoricalIngestionService:
    """Connect provider, Flap adapter, normalized events, and repositories."""

    def __init__(
        self,
        provider: ChainProvider,
        config: FlapConfig,
        *,
        session: Any | None = None,
        provider_name: str | None = None,
    ) -> None:
        self.provider = provider
        self.config = config
        self.session = session
        self.provider_name = provider_name or config.source
        self.adapter = FlapAdapter(provider, config)
        self._last_duplicate_events = 0
        self._chain_validated = False

    async def discover_tokens(
        self, *, from_block: int, to_block: int
    ) -> list[LaunchpadTokenDiscovery]:
        if not self.config.enabled:
            return []
        await self._ensure_bsc_mainnet()
        records: list[LaunchpadTokenDiscovery] = []
        async for record in self.adapter.discover_tokens(
            from_block=from_block,
            to_block=to_block,
        ):
            records.append(record)
        return records

    async def backfill_token(
        self,
        token_address: str,
        *,
        from_block: int,
        to_block: int,
    ) -> FlapBackfillResult:
        if not token_address or not isinstance(token_address, str):
            raise ValueError("token_address is required")
        if from_block < 0 or to_block < from_block:
            raise ValueError("invalid Flap backfill block range")
        discoveries = await self.discover_tokens(from_block=from_block, to_block=to_block)
        discovery = next(
            (
                record
                for record in discoveries
                if record.token_address.lower() == token_address.lower()
            ),
            None,
        )
        if discovery is None:
            raise DataUnavailable("Flap token creation was not found in the requested range")
        if not discovery.creation_tx_hash or discovery.creation_block is None:
            raise DataUnavailable("Flap creation evidence is incomplete")

        creation_receipt = await self.provider.get_transaction_receipt(discovery.creation_tx_hash)
        creation_transaction = await self.provider.get_transaction(discovery.creation_tx_hash)
        if creation_receipt is None:
            raise DataUnavailable("Flap creation receipt is unavailable")
        if creation_transaction is None:
            creation_transaction = self._transaction_from_receipt(
                discovery.creation_tx_hash, creation_receipt
            )
        if self.session is not None:
            # Preserve every receipt/log/block before any decoder can reject a
            # partially understood event. Canonical rows are written later.
            await self._persist_raw(creation_receipt, creation_transaction)
        created_at = discovery.created_at
        token_model = Token(
            address=discovery.token_address,
            chain=self.config.chain,
            launchpad=self.config.launchpad,
            creator_address=discovery.creator_address,
            first_seen_at=created_at,
            status=TokenStatus.NEW.value,
            metadata_json={"creation": discovery.raw},
        )
        creation_log = next(
            (
                log
                for log in creation_receipt.logs
                if log.transaction_hash.lower() == discovery.creation_tx_hash.lower()
                and log.block_number == discovery.creation_block
                and log_matches(
                    log,
                    address=self.config.factory_address,
                    topic=self.config.creation_event_topic,
                )
            ),
            None,
        )
        if creation_log is None:
            raise DataUnavailable("Flap creation receipt does not contain the discovered log")
        creation_index = creation_log.log_index

        candidate_logs = await self._trade_and_lifecycle_logs(from_block, to_block)
        grouped: dict[str, list[ChainLog]] = {}
        for log in candidate_logs:
            grouped.setdefault(log.transaction_hash.lower(), []).append(log)

        trades: list[TradeEvent] = []
        lifecycle: list[TokenLifecycleEvent] = []
        decode_errors = 0
        duplicate_events = self._last_duplicate_events
        for tx_hash in sorted(grouped):
            source_tx_hash = grouped[tx_hash][0].transaction_hash
            receipt = await self.provider.get_transaction_receipt(source_tx_hash)
            transaction = await self.provider.get_transaction(source_tx_hash)
            if receipt is None:
                decode_errors += 1
                continue
            if transaction is None:
                transaction = self._transaction_from_receipt(source_tx_hash, receipt)
            if self.session is not None:
                # Raw evidence is retained even when decoding this transaction
                # fails, so a later ABI correction can replay it.
                await self._persist_raw(receipt, transaction)
            try:
                decoded_trades, decoded_lifecycle = await self.adapter.decode_transaction(
                    transaction, receipt
                )
            except (DataUnavailable, ValueError):
                decode_errors += 1
                continue
            trades.extend(
                event
                for event in decoded_trades
                if event.token_address.lower() == discovery.token_address.lower()
            )
            lifecycle.extend(
                event
                for event in decoded_lifecycle
                if event.token_address.lower() == discovery.token_address.lower()
            )
        persisted_token: Token | None = None
        if self.session is not None:
            persisted_token = await self._persist_token_and_events(
                token_model,
                discovery,
                creation_receipt,
                creation_index,
                trades,
                lifecycle,
            )

        performances = calculate_wallet_token_performance(trades)
        if self.session is not None:
            performance_repo = AsyncWalletTokenPerformanceRepository(self.session)
            for performance in performances:
                await performance_repo.upsert(performance)
            await self.session.commit()
        quality = FlapDataQualityReport.build(
            token_address=discovery.token_address,
            creator_address=discovery.creator_address,
            creation_verified=True,
            trades=trades,
            lifecycle=lifecycle,
            decode_errors=decode_errors,
            duplicate_events=duplicate_events,
        )
        return FlapBackfillResult(
            token_address=discovery.token_address,
            creator_address=discovery.creator_address,
            creation_block=discovery.creation_block,
            creation_tx_hash=discovery.creation_tx_hash,
            created_at=created_at,
            token=persisted_token or token_model,
            trades=tuple(trades),
            lifecycle=tuple(lifecycle),
            performances=tuple(performances),
            quality=quality,
        )

    async def _ensure_bsc_mainnet(self) -> None:
        if self._chain_validated:
            return
        self.config.validate_runtime()
        provider_chain = str(getattr(self.provider, "chain", "bsc")).lower()
        if provider_chain != "bsc":
            raise DataUnavailable(
                f"Flap ingestion requires the BSC provider, received {provider_chain}"
            )
        configured_chain_id = getattr(self.provider, "chain_id", 56)
        if configured_chain_id != 56:
            raise DataUnavailable(
                f"Flap ingestion requires BNB mainnet chain id 56, received {configured_chain_id}"
            )
        get_chain_id = getattr(self.provider, "get_chain_id", None)
        if callable(get_chain_id):
            remote_chain_id = await get_chain_id()
            if remote_chain_id != 56:
                raise DataUnavailable(
                    f"Flap RPC chain id mismatch: expected 56, received {remote_chain_id}"
                )
        self._chain_validated = True

    async def _trade_and_lifecycle_logs(self, from_block: int, to_block: int) -> list[ChainLog]:
        topics: list[str] = []
        for topic in (
            self.config.buy_event_topic,
            self.config.sell_event_topic,
            self.config.trade_event_topic,
            self.config.trading_started_event_topic,
            self.config.near_graduation_event_topic,
            self.config.graduation_event_topic,
            self.config.dex_listed_event_topic,
        ):
            if topic and topic not in topics:
                topics.append(topic)
        if not topics:
            raise DataUnavailable("Flap trade/lifecycle topics are not configured")
        logs: dict[tuple[str, int], ChainLog] = {}
        seen = 0
        for topic in topics:
            lifecycle_topic = topic in {
                self.config.trading_started_event_topic,
                self.config.near_graduation_event_topic,
                self.config.graduation_event_topic,
                self.config.dex_listed_event_topic,
            }
            address = (
                self.config.lifecycle_contract_address
                if lifecycle_topic
                else self.config.trade_contract_address
            )
            if address is None:
                role = "lifecycle" if lifecycle_topic else "trade"
                raise DataUnavailable(
                    f"Flap {role} emitter address is not configured; VERIFY_ON_CHAIN"
                )
            for log in await self.provider.get_logs(
                from_block=from_block,
                to_block=to_block,
                address=address,
                topics=(topic,),
            ):
                seen += 1
                logs[(log.transaction_hash.lower(), log.log_index)] = log
        self._last_duplicate_events = max(0, seen - len(logs))
        return sorted(logs.values(), key=lambda log: (log.block_number, log.log_index))

    async def _persist_raw(
        self, receipt: TransactionReceipt, transaction: ChainTransaction
    ) -> None:
        raw_repo = AsyncRawChainRecordRepository(self.session)
        receipt_payload = dict(receipt.raw)
        receipt_payload.setdefault("transaction_hash", receipt.transaction_hash)
        receipt_payload.setdefault("block_number", receipt.block_number)
        receipt_payload.setdefault(
            "logs",
            [self._raw_log(log) for log in receipt.logs],
        )
        await raw_repo.add_idempotent(
            self.provider_name,
            "transaction_receipt",
            receipt.transaction_hash,
            receipt_payload,
        )
        for log in receipt.logs:
            await raw_repo.add_idempotent(
                self.provider_name,
                "log",
                f"{log.transaction_hash}:{log.log_index}",
                self._raw_log(log),
            )
        block_number = transaction.block_number or receipt.block_number
        block = await self.provider.get_block(block_number, include_transactions=False)
        if block is not None:
            await raw_repo.add_idempotent(
                self.provider_name,
                "block",
                str(block.number),
                dict(block.raw)
                or {
                    "number": block.number,
                    "hash": block.block_hash,
                    "timestamp": int(block.timestamp.timestamp()),
                },
            )
        await self.session.flush()

    async def _persist_token_and_events(
        self,
        token_model: Token,
        discovery: LaunchpadTokenDiscovery,
        creation_receipt: TransactionReceipt,
        creation_index: int,
        trades: Iterable[TradeEvent],
        lifecycle: Iterable[TokenLifecycleEvent],
    ) -> Token:
        token_repo = AsyncTokenRepository(self.session)
        persisted_token = await token_repo.upsert(
            token_model.address,
            chain=token_model.chain,
            launchpad=token_model.launchpad,
            creator_address=token_model.creator_address,
            first_seen_at=token_model.first_seen_at,
            status=token_model.status,
            metadata_json=token_model.metadata_json,
        )
        lifecycle_repo = AsyncTokenLifecycleEventRepository(self.session)
        await lifecycle_repo.add_idempotent(
            TokenLifecycleEvent(
                chain=self.config.chain,
                launchpad=self.config.launchpad,
                token_address=discovery.token_address,
                event_type="CREATED",
                timestamp=discovery.created_at,
                tx_hash=discovery.creation_tx_hash,
                block_number=discovery.creation_block,
                event_index=creation_index,
                payload_json={
                    "source": self.config.source,
                    "evidence_level": "VERIFIED",
                    "raw": discovery.raw,
                },
            )
        )
        trade_repo = AsyncTradeEventRepository(self.session)
        wallet_repo = AsyncWalletRepository(self.session)
        for event in trades:
            await trade_repo.add_idempotent(event)
        for event in lifecycle:
            await lifecycle_repo.add_idempotent(event)
        wallet_events: dict[str, list[TradeEvent]] = defaultdict(list)
        for event in trades:
            wallet_events[event.trader_address].append(event)
        for trader_address, trader_events in wallet_events.items():
            timestamps = [event.timestamp for event in trader_events]
            await wallet_repo.upsert(
                trader_address,
                chain=self.config.chain,
                first_seen_at=min(timestamps),
                last_active_at=max(timestamps),
                total_transactions=len(trader_events),
                total_swaps=len(trader_events),
            )
        first_trade = min((event.timestamp for event in trades), default=None)
        last_seen = max(
            (event.timestamp for event in (*trades, *lifecycle)),
            default=token_model.first_seen_at,
        )
        status = token_model.status
        for event in sorted(lifecycle, key=lambda item: (item.block_number or 0, item.event_index)):
            status = apply_lifecycle_event(status, event.event_type, event=event)
        if trades and status == TokenStatus.NEW.value:
            status = TokenStatus.TRADING.value
        graduation = next(
            (event.timestamp for event in lifecycle if event.event_type.upper() == "GRADUATED"),
            None,
        )
        await token_repo.upsert(
            token_model.address,
            chain=token_model.chain,
            first_trade_at=first_trade,
            last_seen_at=last_seen,
            graduation_at=graduation,
            status=status,
        )
        await self.session.flush()
        return persisted_token

    @staticmethod
    def _raw_log(log: ChainLog) -> dict[str, Any]:
        return {
            "address": log.address,
            "topics": list(log.topics),
            "data": log.data,
            "block_number": log.block_number,
            "transaction_hash": log.transaction_hash,
            "log_index": log.log_index,
            **(log.raw if isinstance(log.raw, dict) else {}),
        }

    @staticmethod
    def _transaction_from_receipt(
        transaction_hash: str, receipt: TransactionReceipt
    ) -> ChainTransaction:
        return ChainTransaction(
            transaction_hash=transaction_hash,
            block_number=receipt.block_number,
            from_address="0x0000000000000000000000000000000000000000",
            to_address=None,
            receipt=receipt,
        )


__all__ = ["FlapBackfillResult", "FlapHistoricalIngestionService"]


async def backfill_flap_token(
    provider: ChainProvider,
    config: FlapConfig,
    token_address: str,
    *,
    from_block: int,
    to_block: int,
    session: Any | None = None,
) -> FlapBackfillResult:
    """Backfill one token using the canonical Flap service."""

    return await FlapHistoricalIngestionService(
        provider,
        config,
        session=session,
    ).backfill_token(
        token_address,
        from_block=from_block,
        to_block=to_block,
    )


__all__.append("backfill_flap_token")

"""Decode Flap creation and lifecycle events."""

from collections.abc import Sequence
from datetime import datetime

from smms.chains.base import ChainLog, ChainTransaction, TransactionReceipt
from smms.db.models import Token, TokenLifecycleEvent, TokenStatus
from smms.ingestion import DataUnavailable

from ._decoding import address_field, log_matches, raw_log, utc_timestamp
from .config import FlapConfig


class FlapLifecycleDecoder:
    """Map configured Flap lifecycle topics to canonical lifecycle events."""

    def __init__(self, config: FlapConfig) -> None:
        self.config = config

    def decode_creation(
        self,
        transaction: ChainTransaction,
        receipt: TransactionReceipt,
        *,
        timestamp: datetime | None = None,
    ) -> Token | TokenLifecycleEvent | None:
        if not self.config.factory_address or not self.config.creation_event_topic:
            raise DataUnavailable("Flap creation configuration is not complete; VERIFY_ON_CHAIN")
        for log in receipt.logs:
            if not log_matches(
                log,
                address=self.config.factory_address,
                topic=self.config.creation_event_topic,
            ):
                continue
            token_address = address_field(
                log,
                topic_index=self.config.creation_token_topic_index,
                data_index=self.config.creation_token_data_index,
                field="creation token",
            )
            creator_address = address_field(
                log,
                topic_index=self.config.creation_creator_topic_index,
                data_index=self.config.creation_creator_data_index,
                field="creation creator",
            )
            if not token_address:
                raise DataUnavailable("Flap creation log does not contain a token address")
            event_timestamp = utc_timestamp(
                timestamp or transaction.timestamp or log.timestamp,
                field="creation timestamp",
            )
            return Token(
                address=token_address,
                chain=self.config.chain,
                launchpad=self.config.launchpad,
                creator_address=creator_address,
                first_seen_at=event_timestamp,
                status=TokenStatus.NEW.value,
                metadata_json={"creation_log": raw_log(log)},
            )
        return None

    def decode_lifecycle(
        self,
        transaction: ChainTransaction,
        receipt: TransactionReceipt,
        *,
        timestamp: datetime | None = None,
    ) -> Sequence[TokenLifecycleEvent]:
        selectors = self._selectors()
        if not selectors:
            return []
        if (
            self.config.lifecycle_token_topic_index is None
            and self.config.lifecycle_token_data_index is None
        ):
            raise DataUnavailable("Flap lifecycle token selector is not configured")

        events: list[TokenLifecycleEvent] = []
        for log in receipt.logs:
            event_type = self._event_type_for_log(log, selectors)
            if event_type is None:
                continue
            token_address = address_field(
                log,
                topic_index=self.config.lifecycle_token_topic_index,
                data_index=self.config.lifecycle_token_data_index,
                field="lifecycle token",
            )
            if not token_address:
                raise DataUnavailable("Flap lifecycle log does not contain a token address")
            event_timestamp = utc_timestamp(
                timestamp or transaction.timestamp or log.timestamp,
                field="lifecycle timestamp",
            )
            events.append(
                TokenLifecycleEvent(
                    chain=self.config.chain,
                    launchpad=self.config.launchpad,
                    token_address=token_address,
                    event_type=event_type,
                    timestamp=event_timestamp,
                    tx_hash=log.transaction_hash or receipt.transaction_hash or transaction.transaction_hash,
                    block_number=(
                        log.block_number
                        if log.block_number is not None
                        else (
                            transaction.block_number
                            if transaction.block_number is not None
                            else receipt.block_number
                        )
                    ),
                    payload_json={
                        "source_external_id": f"{log.transaction_hash}:{log.log_index}",
                        "event_topic": log.topics[0] if log.topics else None,
                        "log": raw_log(log),
                    },
                )
            )
        return events

    def _selectors(self) -> tuple[tuple[str, str], ...]:
        configured = (
            (self.config.trading_started_event_topic, "TRADING_STARTED"),
            (self.config.near_graduation_event_topic, "NEAR_GRADUATION"),
            (self.config.graduation_event_topic, "GRADUATED"),
            (self.config.dex_listed_event_topic, "DEX_LISTED"),
        )
        return tuple((topic, event_type) for topic, event_type in configured if topic)

    def _event_type_for_log(
        self,
        log: ChainLog,
        selectors: tuple[tuple[str, str], ...],
    ) -> str | None:
        for topic, event_type in selectors:
            if log_matches(
                log,
                address=self.config.lifecycle_contract_address,
                topic=topic,
            ):
                return event_type
        return None


__all__ = ["FlapLifecycleDecoder"]

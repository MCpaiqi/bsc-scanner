"""Flap token creation-log discovery."""

from collections.abc import AsyncIterator

from smms.chains.base import ChainLog, ChainProvider
from smms.ingestion import DataUnavailable
from smms.launchpads.base import LaunchpadTokenDiscovery

from ._decoding import address_field, log_matches, raw_log, utc_timestamp
from .config import FlapConfig


class FlapTokenDiscovery:
    """Discover Flap tokens from configured factory creation events."""

    def __init__(self, provider: ChainProvider, config: FlapConfig) -> None:
        self.provider = provider
        self.config = config

    async def discover_tokens(
        self,
        *,
        from_block: int | None = None,
        to_block: int | None = None,
        live: bool = False,
    ) -> AsyncIterator[LaunchpadTokenDiscovery]:
        if not self.config.enabled:
            return
        self._validate_config()
        if live:
            subscribe = getattr(self.provider, "subscribe_logs", None)
            if not callable(subscribe):
                raise DataUnavailable(
                    "Flap live discovery requires a configured chain log subscription"
                )
            stream = subscribe(
                address=self.config.factory_address,
                topics=(self.config.creation_event_topic,),
            )
            async for log in stream:
                yield await self._decode_log(log)
            return

        if from_block is None or to_block is None:
            raise ValueError("from_block and to_block are required for historical discovery")
        if from_block < 0 or to_block < from_block:
            raise ValueError("invalid Flap discovery block range")
        logs = await self.provider.get_logs(
            from_block=from_block,
            to_block=to_block,
            address=self.config.factory_address,
            topics=(self.config.creation_event_topic,),
        )
        ordered = sorted(logs, key=lambda item: (item.block_number, item.log_index))
        for log in ordered:
            if not log_matches(
                log,
                address=self.config.factory_address,
                topic=self.config.creation_event_topic,
            ):
                continue
            yield await self._decode_log(log)

    def _validate_config(self) -> None:
        if not self.config.factory_address:
            raise DataUnavailable("Flap factory address is not configured; VERIFY_ON_CHAIN")
        if not self.config.creation_event_topic:
            raise DataUnavailable("Flap creation event topic is not configured; VERIFY_ON_CHAIN")
        has_token_selector = (
            self.config.creation_token_topic_index is not None
            or self.config.creation_token_data_index is not None
        )
        has_creator_selector = (
            self.config.creation_creator_topic_index is not None
            or self.config.creation_creator_data_index is not None
        )
        if not has_token_selector:
            raise DataUnavailable("Flap creation token selector is not configured")
        if not has_creator_selector:
            raise DataUnavailable("Flap creation creator selector is not configured")

    async def _decode_log(self, log: ChainLog) -> LaunchpadTokenDiscovery:
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
        if not token_address or not creator_address:
            raise DataUnavailable("Flap creation log does not contain token and creator addresses")
        timestamp = log.timestamp
        if timestamp is None:
            block = await self.provider.get_block(log.block_number, include_transactions=False)
            timestamp = block.timestamp if block is not None else None
        created_at = utc_timestamp(timestamp, field="creation block timestamp")
        return LaunchpadTokenDiscovery(
            chain=self.config.chain,
            launchpad=self.config.launchpad,
            token_address=token_address,
            creator_address=creator_address,
            created_at=created_at,
            creation_tx_hash=log.transaction_hash,
            creation_block=log.block_number,
            raw={
                "source_external_id": f"{log.transaction_hash}:{log.log_index}",
                "log": raw_log(log),
            },
        )


__all__ = ["FlapTokenDiscovery"]

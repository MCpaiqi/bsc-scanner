"""Decode configured Flap trade events into the canonical trade model."""

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from smms.chains.base import ChainLog, ChainTransaction, TransactionReceipt
from smms.db.models import TradeEvent, TradeSide
from smms.ingestion import DataUnavailable

from ._decoding import (
    address_field,
    integer_field,
    log_matches,
    raw_log,
    scaled,
    topic_key,
    utc_timestamp,
)
from .config import FlapConfig


class FlapTradeDecoder:
    """Decode only events selected by explicit Flap configuration."""

    def __init__(self, config: FlapConfig) -> None:
        self.config = config

    def decode_trades(
        self,
        transaction: ChainTransaction,
        receipt: TransactionReceipt,
        *,
        timestamp: datetime | None = None,
    ) -> Sequence[TradeEvent]:
        self._validate_config()
        events: list[TradeEvent] = []
        for log in receipt.logs:
            side = self._side_for_log(log)
            if side is None:
                continue
            event_timestamp = utc_timestamp(
                timestamp or transaction.timestamp or log.timestamp,
                field="trade timestamp",
            )
            trader_address = address_field(
                log,
                topic_index=self.config.trade_trader_topic_index,
                data_index=self.config.trade_trader_data_index,
                field="trade trader",
            )
            token_address = address_field(
                log,
                topic_index=self.config.trade_token_topic_index,
                data_index=self.config.trade_token_data_index,
                field="trade token",
            )
            if not trader_address or not token_address:
                raise DataUnavailable("Flap trade log does not contain trader and token addresses")

            token_raw = integer_field(
                log,
                topic_index=self.config.trade_token_amount_topic_index,
                data_index=self.config.trade_token_amount_data_index,
                field="trade token amount",
            )
            native_raw = integer_field(
                log,
                topic_index=self.config.trade_native_amount_topic_index,
                data_index=self.config.trade_native_amount_data_index,
                field="trade native amount",
            )
            token_amount = (
                scaled(
                    token_raw,
                    self.config.token_decimals
                    if self.config.token_decimals is not None
                    else self._require_decimals("token"),
                    field="trade token amount",
                )
                if token_raw is not None
                else None
            )
            native_amount = (
                scaled(
                    native_raw,
                    self.config.native_decimals
                    if self.config.native_decimals is not None
                    else self._require_decimals("native"),
                    field="trade native amount",
                )
                if native_raw is not None
                else None
            )
            price = (
                native_amount / token_amount
                if native_amount is not None and token_amount not in (None, Decimal(0))
                else None
            )
            price_value = self._optional_data_value(
                log,
                self.config.trade_price_data_index,
                "price",
                decimals=self.config.price_decimals,
            )
            if price_value is not None:
                price = price_value
            market_cap_value = self._optional_data_value(
                log,
                self.config.trade_market_cap_data_index,
                "market cap",
                decimals=self.config.market_cap_decimals,
            )
            liquidity = self._optional_data_value(
                log,
                self.config.trade_liquidity_data_index,
                "liquidity",
                decimals=self.config.liquidity_decimals,
            )
            tx_hash = log.transaction_hash or receipt.transaction_hash or transaction.transaction_hash
            if not tx_hash:
                raise DataUnavailable("Flap trade log does not contain a transaction hash")
            events.append(
                TradeEvent(
                    chain=self.config.chain,
                    launchpad=self.config.launchpad,
                    token_address=token_address,
                    trader_address=trader_address,
                    tx_hash=tx_hash,
                    block_number=(
                        log.block_number
                        if log.block_number is not None
                        else (
                            transaction.block_number
                            if transaction.block_number is not None
                            else receipt.block_number
                        )
                    ),
                    timestamp=event_timestamp,
                    side=side,
                    token_amount=token_amount,
                    native_amount=native_amount,
                    price=price,
                    market_cap=market_cap_value,
                    liquidity=liquidity,
                    event_index=log.log_index,
                    source=self.config.source,
                    metadata_json={
                        "source_external_id": f"{tx_hash}:{log.log_index}",
                        "raw_record": {
                            "provider": self.config.source,
                            "record_type": "log",
                            "external_id": f"{tx_hash}:{log.log_index}",
                        },
                        "event_topic": log.topics[0] if log.topics else None,
                        "evidence_level": "VERIFIED",
                        "source": self.config.source,
                        "price_evidence_level": (
                            "VERIFIED"
                            if price_value is not None
                            else ("DERIVED" if price is not None else "UNKNOWN")
                        ),
                        "token_amount_raw": token_raw,
                        "native_amount_raw": native_raw,
                        "log": raw_log(log),
                    },
                )
            )
        return events

    def _validate_config(self) -> None:
        if not (
            self.config.trade_event_topic
            or self.config.buy_event_topic
            or self.config.sell_event_topic
        ):
            raise DataUnavailable("Flap trade event topic is not configured; VERIFY_ON_CHAIN")
        has_side_route = bool(
            self.config.buy_event_topic
            or self.config.sell_event_topic
            or self.config.default_trade_side
            or self.config.trade_side_data_index is not None
            or self.config.trade_side_topic_index is not None
        )
        if not has_side_route:
            raise DataUnavailable(
                "Flap trade direction is not configured; provide buy/sell topics, a side field, "
                "or default_trade_side"
            )
        has_trader_selector = (
            self.config.trade_trader_topic_index is not None
            or self.config.trade_trader_data_index is not None
        )
        has_token_selector = (
            self.config.trade_token_topic_index is not None
            or self.config.trade_token_data_index is not None
        )
        if not has_trader_selector:
            raise DataUnavailable("Flap trade trader selector is not configured")
        if not has_token_selector:
            raise DataUnavailable("Flap trade token selector is not configured")
        if (
            self.config.trade_token_amount_topic_index is None
            and self.config.trade_token_amount_data_index is None
        ):
            raise DataUnavailable("Flap trade token amount selector is not configured")
        if (
            self.config.trade_native_amount_topic_index is None
            and self.config.trade_native_amount_data_index is None
        ):
            raise DataUnavailable("Flap trade native amount selector is not configured")

    def _side_for_log(self, log: ChainLog) -> TradeSide | None:
        if not log_matches(
            log,
            address=self.config.trade_contract_address,
            topic=self.config.buy_event_topic,
        ) and not log_matches(
            log,
            address=self.config.trade_contract_address,
            topic=self.config.sell_event_topic,
        ) and not log_matches(
            log,
            address=self.config.trade_contract_address,
            topic=self.config.trade_event_topic,
        ):
            return None

        topic = topic_key(log.topics[0]) if log.topics else ""
        buy = self.config.buy_event_topic and topic == topic_key(self.config.buy_event_topic)
        sell = self.config.sell_event_topic and topic == topic_key(self.config.sell_event_topic)
        if buy and sell:
            raise DataUnavailable("Flap buy and sell topics must be distinct")
        if buy:
            return TradeSide.BUY
        if sell:
            return TradeSide.SELL

        if (
            self.config.trade_side_data_index is not None
            or self.config.trade_side_topic_index is not None
        ):
            side_value = integer_field(
                log,
                topic_index=self.config.trade_side_topic_index,
                data_index=self.config.trade_side_data_index,
                field="trade side",
            )
            if side_value == self.config.trade_buy_side_value:
                return TradeSide.BUY
            if side_value == self.config.trade_sell_side_value:
                return TradeSide.SELL
            raise DataUnavailable("Flap trade side value is not configured")
        if self.config.default_trade_side:
            return TradeSide(self.config.default_trade_side)
        return None

    @staticmethod
    def _optional_data_value(
        log: ChainLog,
        index: int | None,
        field: str,
        *,
        decimals: int | None,
    ) -> Decimal | None:
        if index is None:
            return None
        value = integer_field(log, topic_index=None, data_index=index, field=field)
        if decimals is None:
            raise DataUnavailable(f"Flap {field} decimals are not configured; VERIFY_ON_CHAIN")
        return scaled(value, decimals, field=field)

    @staticmethod
    def _require_decimals(field: str) -> int:
        raise DataUnavailable(f"Flap {field} decimals are not configured; VERIFY_ON_CHAIN")


__all__ = ["FlapTradeDecoder"]

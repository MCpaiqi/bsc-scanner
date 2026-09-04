from datetime import UTC, datetime
from decimal import Decimal

import pytest

from smms.chains.base import ChainLog, ChainTransaction, TransactionReceipt
from smms.ingestion import DataUnavailable
from smms.launchpads.flap import (
    FlapAdapter,
    FlapBondingCurveAdapter,
    FlapConfig,
    FlapLifecycleDecoder,
    FlapTokenDiscovery,
    FlapTradeDecoder,
)

TOKEN = "0x" + ("11" * 20)
CREATOR = "0x" + ("22" * 20)
WALLET = "0x" + ("33" * 20)


def _topic_address(address: str) -> str:
    return "0x" + ("0" * 24) + address.removeprefix("0x")


def _log(*topics: str, index: int = 0) -> ChainLog:
    return ChainLog(
        address="0xFactory",
        topics=tuple(topics),
        data="0x" + ("0" * 63) + "a" + ("0" * 63) + "5",
        block_number=100,
        transaction_hash="0xTx",
        log_index=index,
    )


def _transaction(receipt: TransactionReceipt) -> ChainTransaction:
    return ChainTransaction(
        transaction_hash="0xTx",
        block_number=100,
        from_address=CREATOR,
        to_address="0xFactory",
        receipt=receipt,
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_flap_discovery_decodes_configured_creation_logs() -> None:
    class Provider:
        async def get_logs(self, **kwargs):
            return [creation_log]

        async def get_block(self, block_number, *, include_transactions=False):
            from smms.chains.base import Block

            return Block(
                number=block_number,
                block_hash="0xBlock",
                timestamp=datetime(2026, 1, 1, tzinfo=UTC),
            )

    creation_log = _log("0xCreate", _topic_address(TOKEN), _topic_address(CREATOR))
    config = FlapConfig(
        factory_address="0xFactory",
        creation_event_topic="0xCreate",
        creation_token_topic_index=1,
        creation_creator_topic_index=2,
    )
    records = [
        record
        async for record in FlapTokenDiscovery(Provider(), config).discover_tokens(
            from_block=100, to_block=100
        )
    ]
    assert records[0].token_address == TOKEN
    assert records[0].creator_address == CREATOR
    assert records[0].launchpad == "FLAP"


def test_flap_trade_decoder_emits_canonical_buy_event() -> None:
    config = FlapConfig(
        trade_contract_address="0xCurve",
        trade_event_topic="0xTrade",
        buy_event_topic="0xTrade",
        trade_trader_topic_index=1,
        trade_token_topic_index=2,
        trade_token_amount_data_index=0,
        trade_native_amount_data_index=1,
        token_decimals=0,
        native_decimals=18,
    )
    log = ChainLog(
        address="0xCurve",
        topics=("0xTrade", _topic_address(WALLET), _topic_address(TOKEN)),
        data="0x" + ("0" * 63) + "a" + ("0" * 63) + "5",
        block_number=100,
        transaction_hash="0xTx",
        log_index=7,
    )
    receipt = TransactionReceipt("0xTx", 100, 1, logs=(log,))
    events = FlapTradeDecoder(config).decode_trades(
        _transaction(receipt), receipt, timestamp=datetime(2026, 1, 1, tzinfo=UTC)
    )
    assert len(events) == 1
    assert events[0].side == "BUY"
    assert events[0].token_address == TOKEN
    assert events[0].trader_address == WALLET
    assert events[0].token_amount == Decimal(10)
    assert events[0].native_amount == Decimal(5) / Decimal(10**18)
    assert events[0].event_index == 7


def test_flap_trade_decoder_emits_sell_event_from_explicit_topic() -> None:
    config = FlapConfig(
        trade_contract_address="0xCurve",
        sell_event_topic="0xSell",
        trade_trader_topic_index=1,
        trade_token_topic_index=2,
        trade_token_amount_data_index=0,
        trade_native_amount_data_index=1,
        token_decimals=0,
        native_decimals=18,
    )
    log = ChainLog(
        address="0xCurve",
        topics=("0xSell", _topic_address(WALLET), _topic_address(TOKEN)),
        data="0x" + ("0" * 63) + "a" + ("0" * 63) + "5",
        block_number=100,
        transaction_hash="0xTx",
        log_index=8,
    )
    receipt = TransactionReceipt("0xTx", 100, 1, logs=(log,))
    events = FlapTradeDecoder(config).decode_trades(
        _transaction(receipt), receipt, timestamp=datetime(2026, 1, 1, tzinfo=UTC)
    )
    assert len(events) == 1
    assert events[0].side == "SELL"


def test_flap_trade_decoder_rejects_ambiguous_generic_trade_topic() -> None:
    config = FlapConfig(
        trade_contract_address="0xCurve",
        trade_event_topic="0xTrade",
        trade_trader_topic_index=1,
        trade_token_topic_index=2,
        trade_token_amount_data_index=0,
        trade_native_amount_data_index=1,
        token_decimals=0,
        native_decimals=18,
    )
    log = ChainLog(
        address="0xCurve",
        topics=("0xTrade", _topic_address(WALLET), _topic_address(TOKEN)),
        data="0x" + ("0" * 63) + "a" + ("0" * 63) + "5",
        block_number=100,
        transaction_hash="0xTx",
        log_index=7,
    )
    receipt = TransactionReceipt("0xTx", 100, 1, logs=(log,))
    with pytest.raises(DataUnavailable):
        FlapTradeDecoder(config).decode_trades(
            _transaction(receipt), receipt, timestamp=datetime(2026, 1, 1, tzinfo=UTC)
        )


@pytest.mark.asyncio
async def test_flap_adapter_resolves_block_timestamp_for_rpc_transactions() -> None:
    class Provider:
        async def get_block(self, block_number, *, include_transactions=False):
            from smms.chains.base import Block

            return Block(
                number=block_number,
                block_hash="0xBlock",
                timestamp=datetime(2026, 1, 1, tzinfo=UTC),
            )

    config = FlapConfig(
        trade_contract_address="0xCurve",
        buy_event_topic="0xTrade",
        trade_trader_topic_index=1,
        trade_token_topic_index=2,
        trade_token_amount_data_index=0,
        trade_native_amount_data_index=1,
        token_decimals=0,
        native_decimals=18,
    )
    log = ChainLog(
        address="0xCurve",
        topics=("0xTrade", _topic_address(WALLET), _topic_address(TOKEN)),
        data="0x" + ("0" * 63) + "a" + ("0" * 63) + "5",
        block_number=100,
        transaction_hash="0xTx",
        log_index=7,
    )
    receipt = TransactionReceipt("0xTx", 100, 1, logs=(log,))
    trades, lifecycle = await FlapAdapter(Provider(), config).decode_transaction(
        _transaction(receipt), receipt
    )
    assert trades[0].timestamp == datetime(2026, 1, 1, tzinfo=UTC)
    assert lifecycle == []


def test_flap_lifecycle_decoder_maps_configured_graduation() -> None:
    config = FlapConfig(
        graduation_event_topic="0xGraduated",
        lifecycle_token_topic_index=1,
    )
    log = _log("0xGraduated", _topic_address(TOKEN))
    receipt = TransactionReceipt("0xTx", 100, 1, logs=(log,))
    events = FlapLifecycleDecoder(config).decode_lifecycle(
        _transaction(receipt), receipt, timestamp=datetime(2026, 1, 1, tzinfo=UTC)
    )
    assert events[0].event_type == "GRADUATED"
    assert events[0].token_address == TOKEN


@pytest.mark.asyncio
async def test_flap_curve_adapter_requires_verified_reader_and_preserves_state() -> None:
    with pytest.raises(DataUnavailable):
        await FlapBondingCurveAdapter().get_state(TOKEN)

    async def reader(token_address: str, block_number: int | None):
        assert token_address == TOKEN
        assert block_number == 100
        return {
            "status": "TRADING",
            "price": Decimal("0.01"),
            "liquidity": Decimal(10),
            "progress": Decimal("0.2"),
            "raw": {"verified": True},
        }

    state = await FlapBondingCurveAdapter(state_reader=reader).get_state(
        TOKEN, block_number=100
    )
    assert state.status.value == "TRADING"
    assert state.price == Decimal("0.01")
    assert state.raw == {"verified": True}

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
    assert events[0].metadata_json["raw_record"] == {
        "provider": "bsc_rpc:flap",
        "record_type": "log",
        "external_id": "0xTx:7",
    }


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


def test_flap_trade_decoder_keeps_price_and_market_cap_distinct() -> None:
    config = FlapConfig(
        trade_contract_address="0xCurve",
        buy_event_topic="0xBuy",
        trade_trader_topic_index=1,
        trade_token_topic_index=2,
        trade_token_amount_data_index=0,
        trade_native_amount_data_index=1,
        trade_price_data_index=2,
        trade_market_cap_data_index=3,
        token_decimals=0,
        native_decimals=0,
        price_decimals=3,
        market_cap_decimals=2,
    )
    log = ChainLog(
        address="0xCurve",
        topics=("0xBuy", _topic_address(WALLET), _topic_address(TOKEN)),
        data=(
            "0x"
            + ("0" * 63)
            + "a"
            + ("0" * 63)
            + "5"
            + ("0" * 62)
            + "7b"
            + ("0" * 60)
            + "3039"
        ),
        block_number=100,
        transaction_hash="0xTx",
        log_index=9,
    )
    receipt = TransactionReceipt("0xTx", 100, 1, logs=(log,))

    events = FlapTradeDecoder(config).decode_trades(
        _transaction(receipt), receipt, timestamp=datetime(2026, 1, 1, tzinfo=UTC)
    )

    assert events[0].price == Decimal("0.123")
    assert events[0].market_cap == Decimal("123.45")


def test_flap_trade_decoder_does_not_guess_optional_value_decimals() -> None:
    config = FlapConfig(
        trade_contract_address="0xCurve",
        buy_event_topic="0xBuy",
        trade_trader_topic_index=1,
        trade_token_topic_index=2,
        trade_token_amount_data_index=0,
        trade_native_amount_data_index=1,
        trade_price_data_index=2,
        token_decimals=0,
        native_decimals=0,
    )
    log = ChainLog(
        address="0xCurve",
        topics=("0xBuy", _topic_address(WALLET), _topic_address(TOKEN)),
        data=(
            "0x"
            + ("0" * 63)
            + "a"
            + ("0" * 63)
            + "5"
            + ("0" * 63)
            + "1"
        ),
        block_number=100,
        transaction_hash="0xTx",
        log_index=10,
    )
    receipt = TransactionReceipt("0xTx", 100, 1, logs=(log,))

    with pytest.raises(DataUnavailable, match="price decimals"):
        FlapTradeDecoder(config).decode_trades(
            _transaction(receipt), receipt, timestamp=datetime(2026, 1, 1, tzinfo=UTC)
        )


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


def test_checked_in_flap_yaml_keeps_verified_selectors_disabled() -> None:
    config = FlapConfig.from_yaml("configs/sources.yaml")

    assert config.enabled is False
    assert config.verification_status == "VERIFY_ON_CHAIN"
    assert config.portal_address == "0xe2cE6ab80874Fa9Fa2aAE65D277Dd6B8e65C9De0"
    assert config.creation_token_data_index == 3
    assert config.creation_creator_data_index == 1
    assert config.trade_token_data_index == 1
    assert config.trade_trader_data_index == 2
    assert config.trade_price_data_index == 6
    assert config.factory_address is None
    assert config.trade_contract_address is None


def test_flap_config_rejects_invalid_decimal_metadata() -> None:
    with pytest.raises(ValueError, match="token_decimals"):
        FlapConfig(token_decimals=256)
    with pytest.raises(ValueError, match="native_decimals"):
        FlapConfig(native_decimals=-1)
    with pytest.raises(TypeError, match="price_decimals"):
        FlapConfig(price_decimals=True)


def test_flap_runtime_validation_rejects_malformed_enabled_selectors() -> None:
    config = FlapConfig(
        enabled=True,
        verification_status="VERIFIED",
        factory_address="0xFactory",
        creation_event_topic="0x" + ("aa" * 32),
    )

    with pytest.raises(ValueError, match="factory_address"):
        config.validate_runtime()


def test_flap_config_requires_explicit_verified_status_for_runtime() -> None:
    config = FlapConfig(enabled=True)

    with pytest.raises(ValueError, match="verification_status must be VERIFIED"):
        config.validate_runtime()


def test_flap_config_reads_nested_verification_status() -> None:
    config = FlapConfig.from_mapping(
        {"launchpads": {"flap": {"verification": {"status": "verified"}}}}
    )

    assert config.verification_status == "VERIFIED"


def test_flap_address_decoder_rejects_non_zero_abi_padding() -> None:
    from smms.launchpads.flap._decoding import address_from_topic

    with pytest.raises(DataUnavailable, match="padding"):
        address_from_topic("0x" + ("01" * 12) + ("11" * 20))


def test_flap_data_quality_report_handles_missing_trade_facts() -> None:
    from smms.db.models import TradeEvent
    from smms.ingestion.flap import FlapDataQualityReport

    event = TradeEvent(
        chain="bsc",
        launchpad="FLAP",
        token_address=TOKEN,
        trader_address=WALLET,
        tx_hash="0xTx",
        timestamp=None,
        side="BUY",
        source="fixture",
        event_index=1,
    )

    report = FlapDataQualityReport.build(
        token_address=TOKEN,
        creation_verified=True,
        trades=[event],
        lifecycle=[],
    )

    assert report.missing_timestamps == 1
    assert report.first_trade is None
    assert report.creator_address is None
    assert "Creator: UNKNOWN" in report.render()
    assert "Unknown Events: UNKNOWN" in report.render()
    assert report.overall == "FAIL"


def test_flap_data_quality_report_rejects_buy_only_as_success() -> None:
    from smms.db.models import TradeEvent
    from smms.ingestion.flap import FlapDataQualityReport

    event = TradeEvent(
        chain="bsc",
        launchpad="FLAP",
        token_address=TOKEN,
        trader_address=WALLET,
        tx_hash="0xTx",
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        side="BUY",
        token_amount=Decimal(1),
        native_amount=Decimal(1),
        source="fixture",
        event_index=1,
    )

    report = FlapDataQualityReport.build(
        token_address=TOKEN,
        creator_address=CREATOR,
        creation_verified=True,
        trades=[event],
        lifecycle=[],
    )

    assert report.overall == "FAIL"


def test_flap_data_quality_report_counts_explicit_unknown_events() -> None:
    from smms.db.models import TokenLifecycleEvent
    from smms.ingestion.flap import FlapDataQualityReport

    event = TokenLifecycleEvent(
        chain="bsc",
        launchpad="FLAP",
        token_address=TOKEN,
        event_type="UNKNOWN",
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        tx_hash="0xUnknown",
        event_index=0,
        payload_json={"evidence_level": "UNKNOWN"},
    )

    report = FlapDataQualityReport.build(
        token_address=TOKEN,
        creator_address=CREATOR,
        creation_verified=True,
        trades=[],
        lifecycle=[event],
    )

    assert report.unknown_events == 1
    assert report.overall == "FAIL"


def test_wallet_performance_keeps_derived_values_unknown_when_amount_is_missing() -> None:
    from smms.db.models import TradeEvent, TradeSide
    from smms.ingestion.flap import calculate_wallet_token_performance

    event = TradeEvent(
        chain="bsc",
        launchpad="FLAP",
        token_address=TOKEN,
        trader_address=WALLET,
        tx_hash="0xTx",
        block_number=1,
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        side=TradeSide.BUY,
        token_amount=Decimal(1),
        native_amount=None,
        price=Decimal(1),
        source="fixture",
        event_index=0,
    )

    performance = calculate_wallet_token_performance([event])[0]

    assert performance.position_size is None
    assert performance.total_buy is None
    assert performance.total_sell is None
    assert performance.realized_pnl is None
    assert performance.unrealized_pnl is None
    assert performance.roi is None
    assert performance.multiple is None


def test_wallet_performance_does_not_treat_out_of_window_sell_as_profit() -> None:
    from smms.db.models import TradeEvent, TradeSide
    from smms.ingestion.flap import calculate_wallet_token_performance

    event = TradeEvent(
        chain="bsc",
        launchpad="FLAP",
        token_address=TOKEN,
        trader_address=WALLET,
        tx_hash="0xSell",
        block_number=1,
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        side=TradeSide.SELL,
        token_amount=Decimal(1),
        native_amount=Decimal(1),
        price=Decimal(1),
        source="fixture",
        event_index=0,
    )

    performance = calculate_wallet_token_performance([event])[0]

    assert performance.total_sell is None
    assert performance.realized_pnl is None
    assert performance.roi is None


def test_wallet_performance_marks_sell_beyond_known_inventory_unknown() -> None:
    from smms.db.models import TradeEvent, TradeSide
    from smms.ingestion.flap import calculate_wallet_token_performance

    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    events = [
        TradeEvent(
            chain="bsc",
            launchpad="FLAP",
            token_address=TOKEN,
            trader_address=WALLET,
            tx_hash="0xBuy",
            block_number=1,
            timestamp=timestamp,
            side=TradeSide.BUY,
            token_amount=Decimal(1),
            native_amount=Decimal(1),
            price=Decimal(1),
            source="fixture",
            event_index=0,
        ),
        TradeEvent(
            chain="bsc",
            launchpad="FLAP",
            token_address=TOKEN,
            trader_address=WALLET,
            tx_hash="0xSell",
            block_number=2,
            timestamp=timestamp,
            side=TradeSide.SELL,
            token_amount=Decimal(2),
            native_amount=Decimal(2),
            price=Decimal(1),
            source="fixture",
            event_index=0,
        ),
    ]

    performance = calculate_wallet_token_performance(events)[0]

    assert performance.position_size is None
    assert performance.realized_pnl is None
    assert performance.multiple is None

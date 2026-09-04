from datetime import UTC, datetime
from decimal import Decimal

import pytest

from smms.chains.base import Block, ChainLog, ChainTransaction, TransactionReceipt
from smms.db.models import TradeEvent, TradeSide
from smms.ingestion.flap import FlapHistoricalIngestionService, calculate_wallet_token_performance
from smms.launchpads.flap import FlapConfig

TOKEN = "0x" + ("11" * 20)
CREATOR = "0x" + ("22" * 20)
WALLET = "0x" + ("33" * 20)
FACTORY = "0x" + ("44" * 20)
CURVE = "0x" + ("55" * 20)
CREATE_TOPIC = "0x" + ("aa" * 32)
BUY_TOPIC = "0x" + ("bb" * 32)
SELL_TOPIC = "0x" + ("cc" * 32)


def _word(value: int) -> str:
    return f"{value:064x}"


def _address_word(address: str) -> str:
    return address.removeprefix("0x").rjust(64, "0")


def _data(*words: str) -> str:
    return "0x" + "".join(words)


def _log(
    *,
    address: str,
    topic: str,
    data: str,
    tx_hash: str,
    block_number: int,
    log_index: int,
) -> ChainLog:
    return ChainLog(
        address=address,
        topics=(topic,),
        data=data,
        block_number=block_number,
        transaction_hash=tx_hash,
        log_index=log_index,
        raw={"fixture": True},
    )


class FixtureProvider:
    chain = "bsc"
    chain_id = 56

    def __init__(self) -> None:
        self.creation = _log(
            address=FACTORY,
            topic=CREATE_TOPIC,
            data=_data(_word(1), _address_word(CREATOR), _word(1_000_000_000), _address_word(TOKEN)),
            tx_hash="0xCreate",
            block_number=100,
            log_index=1,
        )
        self.buy = _log(
            address=CURVE,
            topic=BUY_TOPIC,
            data=_data(
                _word(101),
                _address_word(TOKEN),
                _address_word(WALLET),
                _word(2 * 10**18),
                _word(10**16),
                _word(10**14),
                _word(5 * 10**15),
            ),
            tx_hash="0xBuy",
            block_number=101,
            log_index=2,
        )
        self.sell = _log(
            address=CURVE,
            topic=SELL_TOPIC,
            data=_data(
                _word(102),
                _address_word(TOKEN),
                _address_word(WALLET),
                _word(1 * 10**18),
                _word(7 * 10**15),
                _word(7 * 10**13),
                _word(7 * 10**15),
            ),
            tx_hash="0xSell",
            block_number=102,
            log_index=3,
        )
        self.receipts = {
            "0xCreate": TransactionReceipt("0xCreate", 100, 1, logs=(self.creation,)),
            "0xBuy": TransactionReceipt("0xBuy", 101, 1, logs=(self.buy,)),
            "0xSell": TransactionReceipt("0xSell", 102, 1, logs=(self.sell,)),
        }
        self.transactions = {
            tx_hash: ChainTransaction(
                transaction_hash=tx_hash,
                block_number=receipt.block_number,
                from_address=WALLET,
                to_address=CURVE,
                receipt=receipt,
            )
            for tx_hash, receipt in self.receipts.items()
        }

    async def get_latest_block_number(self) -> int:
        return 102

    async def get_block(self, block_number: int, *, include_transactions: bool = False) -> Block:
        return Block(
            number=block_number,
            block_hash=f"0xBlock{block_number}",
            timestamp=datetime(2026, 1, 1, 0, block_number - 100, tzinfo=UTC),
        )

    async def get_transaction(self, transaction_hash: str) -> ChainTransaction | None:
        return self.transactions.get(transaction_hash)

    async def get_transaction_receipt(self, transaction_hash: str) -> TransactionReceipt | None:
        return self.receipts.get(transaction_hash)

    async def get_logs(self, *, from_block: int, to_block: int, address=None, topics=None):
        topic = topics[0] if topics else None
        logs = [self.creation, self.buy, self.sell]
        return [
            log
            for log in logs
            if from_block <= log.block_number <= to_block
            and (address is None or log.address.lower() == address.lower())
            and (topic is None or log.topics[0].lower() == topic.lower())
        ]


def _config() -> FlapConfig:
    return FlapConfig(
        verification_status="VERIFIED",
        factory_address=FACTORY,
        creation_event_topic=CREATE_TOPIC,
        creation_token_data_index=3,
        creation_creator_data_index=1,
        trade_contract_address=CURVE,
        buy_event_topic=BUY_TOPIC,
        sell_event_topic=SELL_TOPIC,
        trade_trader_data_index=2,
        trade_token_data_index=1,
        trade_token_amount_data_index=3,
        trade_native_amount_data_index=4,
        trade_price_data_index=6,
        token_decimals=18,
        native_decimals=18,
        price_decimals=18,
    )


@pytest.mark.asyncio
async def test_single_token_backfill_decodes_creation_buy_sell_and_quality() -> None:
    result = await FlapHistoricalIngestionService(FixtureProvider(), _config()).backfill_token(
        TOKEN,
        from_block=100,
        to_block=102,
    )

    assert result.token_address == TOKEN
    assert result.creator_address == CREATOR
    assert result.creation_block == 100
    assert [event.side.value for event in result.trades] == ["BUY", "SELL"]
    assert result.trades[0].token_amount == Decimal(2)
    assert result.trades[1].native_amount == Decimal("0.007")
    assert result.quality.buy_count == 1
    assert result.quality.sell_count == 1
    assert result.quality.unique_traders == 1
    assert result.quality.decode_errors == 0
    assert result.quality.overall == "PASS_WITH_WARNINGS"


def test_wallet_token_performance_uses_weighted_cost_and_historical_thresholds() -> None:
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
            token_amount=Decimal(10),
            native_amount=Decimal(10),
            price=Decimal(1),
            source="fixture",
            event_index=0,
        ),
        TradeEvent(
            chain="bsc",
            launchpad="FLAP",
            token_address=TOKEN,
            trader_address=WALLET,
            tx_hash="0xSell1",
            block_number=2,
            timestamp=timestamp.replace(minute=1),
            side=TradeSide.SELL,
            token_amount=Decimal(5),
            native_amount=Decimal(15),
            price=Decimal(3),
            source="fixture",
            event_index=0,
        ),
        TradeEvent(
            chain="bsc",
            launchpad="FLAP",
            token_address=TOKEN,
            trader_address=WALLET,
            tx_hash="0xSell2",
            block_number=3,
            timestamp=timestamp.replace(minute=2),
            side=TradeSide.SELL,
            token_amount=Decimal(5),
            native_amount=Decimal("2.5"),
            price=Decimal("0.5"),
            source="fixture",
            event_index=0,
        ),
    ]

    performance = calculate_wallet_token_performance(events)[0]

    assert performance.realized_pnl == Decimal("7.5")
    assert performance.position_size == Decimal(0)
    assert performance.multiple == Decimal("1.75")
    assert performance.reached_2x is True

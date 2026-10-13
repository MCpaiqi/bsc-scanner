from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from smms.chains.base import ChainLog
from smms.db.models import Base, MarketEvent, Pair
from smms.market import PancakeSwapAdapter, ProtocolRegistry

PAIR_TOPIC = PancakeSwapAdapter().config.pair_created_topic
SWAP_TOPIC = PancakeSwapAdapter().config.swap_topic


def word(value: int) -> str:
    return f"{value:064x}"


def test_pair_created_decoding_is_deterministic():
    adapter = PancakeSwapAdapter()
    log = ChainLog(
        address="0xFactory",
        topics=(PAIR_TOPIC, "0x" + word(1), "0x" + word(2)),
        data="0x" + word(3) + word(1),
        block_number=10,
        transaction_hash="0xTx",
        log_index=0,
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert adapter.decode_pair_created(log) == adapter.decode_pair_created(log)
    assert adapter.decode_pair_created(log).address == "0x" + ("03".zfill(40))


def test_swap_preserves_unknown_when_pair_orientation_is_unavailable():
    adapter = PancakeSwapAdapter()
    log = ChainLog(
        address="0xPair",
        topics=(SWAP_TOPIC, "0x" + word(9)),
        data="0x" + word(1) + word(0) + word(0) + word(2),
        block_number=11,
        transaction_hash="0xSwap",
        log_index=1,
    )
    event = adapter.decode_swap(log)
    assert event is not None
    assert event.side.value == "UNKNOWN"
    assert event.wallet == "0x" + "09".zfill(40)


def test_unknown_protocol_is_detectable_without_throwing():
    assert ProtocolRegistry().detect(
        ChainLog("0xC", ("0xunknown",), "0x", 1, "0xTx", 0)
    ) is None


def test_market_tables_are_part_of_schema():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Pair(chain_id=56, address="0xPair", token0_address="0x0", token1_address="0x1", dex="pancakeswap"))
        session.add(MarketEvent(chain_id=56, event_type="UNKNOWN", tx_hash="0xTx", timestamp=datetime.now(UTC), source="fixture"))
        session.commit()
        assert session.query(Pair).count() == 1
        assert session.query(MarketEvent).count() == 1

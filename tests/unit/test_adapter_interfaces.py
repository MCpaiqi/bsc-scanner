from datetime import UTC, datetime
from decimal import Decimal

from smms.chains.base import Block, ChainLog, ChainTransaction, TransactionReceipt
from smms.launchpads.base import (
    BondingCurveState,
    BondingCurveStatus,
    LaunchpadAdapter,
    LaunchpadTokenDiscovery,
    apply_lifecycle_event,
    lifecycle_status,
)


def test_chain_base_models_preserve_raw_chain_context() -> None:
    log = ChainLog(
        address="0xContract",
        topics=("0xTopic0",),
        data="0xData",
        block_number=100,
        transaction_hash="0xTx",
        log_index=2,
    )
    receipt = TransactionReceipt(
        transaction_hash="0xTx",
        block_number=100,
        status=1,
        logs=(log,),
        gas_used=Decimal(21000),
    )
    transaction = ChainTransaction(
        transaction_hash="0xTx",
        block_number=100,
        from_address="0xWallet",
        to_address="0xContract",
        value=Decimal("0.1"),
        input_data="0xCall",
        receipt=receipt,
    )
    block = Block(
        number=100,
        block_hash="0xBlock",
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        transactions=(transaction,),
    )
    assert block.transactions[0].receipt.logs[0].log_index == 2
    assert block.transactions[0].raw == {}


def test_launchpad_base_models_are_chain_neutral() -> None:
    discovery = LaunchpadTokenDiscovery(
        chain="bsc",
        launchpad="FLAP",
        token_address="0xToken",
        creator_address="0xCreator",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        creation_tx_hash="0xCreate",
        creation_block=100,
    )
    state = BondingCurveState(
        status=BondingCurveStatus.TRADING,
        price=Decimal("0.01"),
        liquidity=Decimal(10),
        progress=Decimal("0.2"),
    )
    assert discovery.launchpad == "FLAP"
    assert state.status is BondingCurveStatus.TRADING
    assert state.raw == {}


def test_launchpad_adapter_protocol_is_small_and_composes_capabilities() -> None:
    required = {
        "chain",
        "launchpad",
        "discover_tokens",
        "decode_creation",
        "decode_trades",
        "decode_lifecycle",
        "get_state",
    }
    members = set(LaunchpadAdapter.__annotations__)
    for base in LaunchpadAdapter.__mro__:
        members.update(base.__dict__)
    assert required <= members


def test_lifecycle_mapping_preserves_unknown_and_monotonic_states() -> None:
    assert lifecycle_status("created") == "NEW"
    assert lifecycle_status("vendor_specific_event") == "UNKNOWN"
    assert apply_lifecycle_event("TRADING", "CREATED") == "TRADING"
    assert apply_lifecycle_event("TRADING", "GRADUATED") == "GRADUATED"
    assert apply_lifecycle_event("GRADUATED", "UNKNOWN") == "GRADUATED"

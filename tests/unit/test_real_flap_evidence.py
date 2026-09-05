from datetime import UTC, datetime

import pytest

from smms.chains.base import Block, ChainLog
from smms.ingestion import DataUnavailable
from smms.launchpads.flap.verification import (
    TOKEN_BOUGHT,
    verify_contract,
    verify_event_schema,
    verify_real_log,
)


def _word(value: int) -> str:
    return f"{value:064x}"


def _address_word(address: str) -> str:
    return address.removeprefix("0x").rjust(64, "0")


class Provider:
    chain = "bsc"
    chain_id = 56

    async def get_chain_id(self) -> int:
        return 56

    async def get_code(self, address: str, *, block_number: int | None = None) -> str:
        return "0x6000"

    async def get_block_timestamp(self, block_number: int) -> datetime:
        return datetime(2026, 1, 1, tzinfo=UTC)

    async def get_block(self, block_number: int, *, include_transactions: bool = False) -> Block:
        return Block(block_number, "0xblock", datetime(2026, 1, 1, tzinfo=UTC))


@pytest.mark.asyncio
async def test_real_log_verification_decodes_registered_trade_fields() -> None:
    token = "0x" + "11" * 20
    trader = "0x" + "22" * 20
    log = ChainLog(
        address="0x" + "33" * 20,
        topics=(TOKEN_BOUGHT.topic0,),
        data="0x"
        + "".join(
            (
                _word(100),
                _address_word(token),
                _address_word(trader),
                _word(2),
                _word(3),
                _word(1),
                _word(4),
            )
        ),
        block_number=10,
        transaction_hash="0x" + "aa" * 32,
        log_index=5,
    )
    evidence = await verify_real_log(
        Provider(), log, event=TOKEN_BOUGHT, expected_emitter=log.address
    )
    assert evidence.decoded_fields["token"] == token
    assert evidence.decoded_fields["trader"] == trader
    assert evidence.block_timestamp == datetime(2026, 1, 1, tzinfo=UTC)
    assert evidence.evidence_level.value == "UNKNOWN"


def test_event_schema_rejects_wrong_topic() -> None:
    with pytest.raises(DataUnavailable):
        verify_event_schema(
            TOKEN_BOUGHT,
            topics=("0x" + "00" * 32,),
            data="0x",
            expected_emitter="0x" + "33" * 20,
        )


def test_event_schema_requires_emitter() -> None:
    with pytest.raises(DataUnavailable):
        verify_event_schema(TOKEN_BOUGHT, topics=(TOKEN_BOUGHT.topic0,), data="0x")


@pytest.mark.asyncio
async def test_verify_contract_requires_deployed_bytecode() -> None:
    result = await verify_contract(Provider(), "0x" + "44" * 20)
    assert result["chain_id"] == 56
    assert result["bytecode_evidence_level"] == "VERIFIED"
    assert result["evidence_level"] == "UNKNOWN"

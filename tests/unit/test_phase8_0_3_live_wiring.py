from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from smms.chains.base import ChainLog
from smms.db.models import Base, Token, TokenMarketSnapshot
from smms.market import PancakeSwapAdapter, canonical_event_topic, materialize_dexscreener_snapshot
from smms.risk.engine import analyze_token_risk


def word(value: int) -> str:
    return f"{value:064x}"


def test_canonical_topics_are_real_keccak_values_and_no_placeholders():
    adapter = PancakeSwapAdapter()
    assert adapter.config.swap_topic == canonical_event_topic("Swap(address,uint256,uint256,uint256,uint256,address)")
    assert adapter.config.sync_topic == canonical_event_topic("Sync(uint112,uint112)")
    assert "f3f3f3f3" not in adapter.config.swap_topic
    assert "0011223344" not in adapter.config.mint_topic


def test_swap_decode_fixture_preserves_sender_and_amounts():
    adapter = PancakeSwapAdapter()
    log = ChainLog(
        address="0x" + "a" * 40,
        topics=(adapter.config.swap_topic, "0x" + word(7), "0x" + word(8)),
        data="0x" + word(0) + word(100) + word(50) + word(0),
        block_number=100,
        transaction_hash="0xtx",
        log_index=2,
    )
    decoded = adapter.decode_swap(log)
    assert decoded is not None
    assert decoded.wallet == "0x" + "0" * 39 + "7"
    assert decoded.metadata["amounts_raw"] == [0, 100, 50, 0]


def test_dexscreener_snapshot_keeps_provider_provenance_and_contract():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        row = materialize_dexscreener_snapshot(
            session,
            {
                "token_address": "0x" + "1" * 40,
                "pair_address": "0x" + "2" * 40,
                "dex_id": "pancakeswap",
                "price_usd": 1.25,
                "market_cap_usd": 100000,
                "liquidity_usd": 25000,
                "volume_m5": 5000,
                "txns_m5": {"buys": 4, "sells": 2},
            },
        )
        session.commit()
        assert row.volume_5m == Decimal(5000)
        assert row.source == "DEXSCREENER"
        assert session.query(TokenMarketSnapshot).count() == 1


def test_orm_token_external_liquidity_reaches_risk_engine():
    token = Token(address="0x" + "3" * 40, chain="bsc", metadata_json={})
    result = analyze_token_risk(
        token,
        resolved_evidence={"liquidity_usd": {"resolved_value": 25000}},
        as_of=datetime.now(UTC),
    )
    assert result.liquidity_risk.level != "UNKNOWN"
    assert result.liquidity_risk.details["current_liquidity"] == Decimal(25000)


@pytest.mark.asyncio
async def test_metadata_failure_is_fail_closed():
    class Provider:
        async def eth_call(self, *_args):
            raise TimeoutError("provider down")

    from smms.market.metadata import enrich_erc20_metadata

    result = await enrich_erc20_metadata(Provider(), "0x" + "4" * 40, retries=0)
    assert result["metadata_status"] == "UNKNOWN"
    assert result["metadata_reason_code"] == "METADATA_PROVIDER_TIMEOUT"

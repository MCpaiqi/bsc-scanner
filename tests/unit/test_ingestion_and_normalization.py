import json
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from smms.config import Settings
from smms.ingestion import DataUnavailable, MockProvider
from smms.ingestion.solana import SolanaRPCProvider
from smms.normalization import (
    NormalizationError,
    normalize_swap,
    normalize_token,
    normalize_transaction,
)


@pytest.mark.asyncio
async def test_mock_provider_is_deterministic_and_missing_data_is_explicit() -> None:
    provider = MockProvider(tokens={"Token111": {"address": "Token111", "decimals": 6}})
    assert await provider.get_token("Token111") == {"address": "Token111", "decimals": 6}
    with pytest.raises(DataUnavailable):
        await provider.get_token("Unknown")


def test_transaction_normalization_preserves_raw_and_normalized_amounts() -> None:
    result = normalize_transaction(
        {
            "signature": "sig-1",
            "wallet_address": "Wallet111",
            "block_time": 1735689600,
            "slot": 10,
            "amount_in_raw": "1500000",
            "amount_out_raw": 2500000,
            "decimals_in": 6,
            "decimals_out": 6,
            "side": "BUY",
        }
    )
    assert result["timestamp"].tzinfo == UTC
    assert result["amount_in_raw"] == Decimal(1500000)
    assert result["amount_in"] == Decimal("1.5")
    assert result["amount_out"] == Decimal("2.5")


def test_normalizers_reject_missing_authoritative_fields() -> None:
    with pytest.raises(NormalizationError):
        normalize_token({})
    with pytest.raises(NormalizationError):
        normalize_transaction({"signature": "sig-1", "wallet_address": "Wallet111"})
    with pytest.raises(NormalizationError):
        normalize_swap({"signature": "sig-1", "wallet_address": "Wallet111", "timestamp": "bad"})


def test_swap_normalization_accepts_utc_datetime() -> None:
    result = normalize_swap(
        {
            "signature": "sig-1",
            "wallet_address": "Wallet111",
            "timestamp": datetime(2025, 1, 1, tzinfo=UTC),
            "usd_value": "12.50",
        }
    )
    assert result["timestamp"].tzinfo == UTC
    assert result["usd_value"] == Decimal("12.50")


@pytest.mark.asyncio
async def test_solana_rpc_provider_uses_standard_json_rpc_methods() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        method = json.loads(request.content)["method"]
        calls.append(method)
        if method == "getAccountInfo":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {
                        "value": {
                            "owner": "Tokenkeg",
                            "data": {"parsed": {"info": {"decimals": 6}}},
                        }
                    },
                },
            )
        if method == "getSignaturesForAddress":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": [{"signature": "sig-1", "slot": 4, "blockTime": 1735689600}],
                },
            )
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"slot": 4, "blockTime": 1735689600}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = SolanaRPCProvider(Settings(_env_file=None, SOLANA_RPC_URL="https://rpc.example"), client)
    token = await provider.get_token("Token111")
    transactions = await provider.get_transactions("Wallet111", limit=1)
    await client.aclose()
    assert token["decimals"] == 6
    assert transactions[0]["signature"] == "sig-1"
    assert calls == ["getAccountInfo", "getSignaturesForAddress", "getTransaction"]

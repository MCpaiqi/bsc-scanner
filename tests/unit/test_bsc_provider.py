import json
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from smms.chains.base import ChainProvider
from smms.chains.bsc import BSCProvider
from smms.config import Settings
from smms.ingestion import DataUnavailable


@pytest.mark.asyncio
async def test_bsc_provider_normalizes_blocks_transactions_receipts_and_logs() -> None:
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        methods.append(payload["method"])
        method = payload["method"]
        if method == "eth_blockNumber":
            result = "0x64"
        elif method == "eth_getBlockByNumber":
            result = {
                "number": "0x64",
                "hash": "0xBlock",
                "timestamp": "0x65a00000",
                "transactions": [
                    {
                        "hash": "0xTx",
                        "blockNumber": "0x64",
                        "from": "0xWallet",
                        "to": "0xContract",
                        "value": "0x16345785d8a0000",
                        "input": "0xCall",
                    }
                ],
            }
        elif method == "eth_getTransactionByHash":
            result = {
                "hash": "0xTx",
                "blockNumber": "0x64",
                "from": "0xWallet",
                "to": "0xContract",
                "value": "0x16345785d8a0000",
                "input": "0xCall",
            }
        elif method == "eth_getTransactionReceipt":
            result = {
                "transactionHash": "0xTx",
                "blockNumber": "0x64",
                "status": "0x1",
                "gasUsed": "0x5208",
                "logs": [
                    {
                        "address": "0xContract",
                        "topics": ["0xTopic"],
                        "data": "0xData",
                        "blockNumber": "0x64",
                        "transactionHash": "0xTx",
                        "transactionIndex": "0x0",
                        "logIndex": "0x2",
                    }
                ],
            }
        elif method == "eth_getLogs":
            result = [
                {
                    "address": "0xContract",
                    "topics": ["0xTopic"],
                    "data": "0xData",
                    "blockNumber": "0x64",
                    "transactionHash": "0xTx",
                    "transactionIndex": "0x0",
                    "logIndex": "0x2",
                }
            ]
        else:
            result = None
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": result})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = BSCProvider(
        Settings(_env_file=None, BSC_RPC_URL="https://bsc.example"), client=client
    )
    assert isinstance(provider, ChainProvider)
    assert await provider.get_latest_block_number() == 100
    block = await provider.get_block(100, include_transactions=True)
    transaction = await provider.get_transaction("0xTx")
    receipt = await provider.get_transaction_receipt("0xTx")
    logs = await provider.get_logs(from_block=100, to_block=100, address="0xContract")
    await client.aclose()

    assert block is not None
    assert block.timestamp.tzinfo is UTC
    assert block.timestamp == datetime.fromtimestamp(0x65A00000, tz=UTC)
    assert block.transactions[0].value == Decimal("0.1")
    assert transaction is not None
    assert transaction.transaction_hash == "0xTx"
    assert receipt is not None
    assert receipt.logs[0].log_index == 2
    assert logs[0].transaction_hash == "0xTx"
    assert methods == [
        "eth_blockNumber",
        "eth_getBlockByNumber",
        "eth_getTransactionByHash",
        "eth_getTransactionReceipt",
        "eth_getLogs",
    ]


@pytest.mark.asyncio
async def test_bsc_provider_reports_missing_configuration_and_rpc_errors() -> None:
    with pytest.raises(DataUnavailable):
        BSCProvider(Settings(_env_file=None))

    def error_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "down"}},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(error_handler))
    provider = BSCProvider(
        Settings(_env_file=None, BSC_RPC_URL="https://bsc.example"), client=client
    )
    with pytest.raises(DataUnavailable):
        await provider.get_latest_block_number()
    await client.aclose()

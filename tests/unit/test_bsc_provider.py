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
    assert block.transactions[0].timestamp == block.timestamp
    assert transaction is not None
    assert transaction.transaction_hash == "0xTx"
    assert receipt is not None
    assert receipt.logs[0].log_index == 2
    assert receipt.logs[0].timestamp == block.timestamp
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


@pytest.mark.asyncio
async def test_bsc_provider_preserves_topic0_or_filter_for_pair_events() -> None:
    """V2 pair events share topic0, so alternatives must occupy one topic slot."""
    observed: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload["method"] == "eth_getLogs":
            observed.update(payload["params"][0])
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": []})
        if payload["method"] == "eth_chainId":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": "0x38"})
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": "0x64"})

    swap, sync = "0xswap", "0xsync"
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = BSCProvider(Settings(_env_file=None, BSC_RPC_URL="https://bsc.example"), client=client)
        await provider.get_logs(
            from_block=100, to_block=100, address=("0xPair",), topics=((swap, sync),)
        )

    assert observed["topics"] == [[swap, sync]]


@pytest.mark.asyncio
async def test_bsc_provider_records_bounded_get_logs_request_metrics() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["method"] == "eth_getLogs"
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = BSCProvider(Settings(_env_file=None, BSC_RPC_URL="https://bsc.example"), client=client)
        await provider.get_logs(from_block=100, to_block=100)

    assert len(provider.last_get_logs_metrics) == 1
    metric = provider.last_get_logs_metrics[0]
    assert metric["method"] == "eth_getLogs"
    assert metric["endpoint_index"] == 0
    assert metric["http_status"] == 200
    assert metric["retry_count"] == 0
    assert metric["latency_ms"] >= 0


@pytest.mark.asyncio
async def test_bsc_provider_records_transaction_request_metrics() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload["method"] == "eth_getTransactionByHash":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": None})
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": "0x38"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = BSCProvider(Settings(_env_file=None, BSC_RPC_URL="https://bsc.example"), client=client)
        await provider.get_transaction("0xtx")

    assert len(provider.last_request_metrics) == 1
    assert provider.last_request_metrics[0]["method"] == "eth_getTransactionByHash"


@pytest.mark.asyncio
async def test_bsc_provider_resets_request_metric_window_between_chunks() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": "0x64"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = BSCProvider(Settings(_env_file=None, BSC_RPC_URL="https://bsc.example"), client=client)
        await provider.get_latest_block_number()
        provider.reset_request_metrics()
        await provider.get_latest_block_number()

    assert len(provider.last_request_metrics) == 1
    assert provider.last_request_metrics[0]["method"] == "eth_blockNumber"


@pytest.mark.asyncio
@pytest.mark.parametrize("with_fallback", [False, True])
async def test_bsc_provider_recovers_logs_after_remote_eof(with_fallback: bool) -> None:
    hosts: list[str] = []
    payloads: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        payloads.append(json.loads(request.content))
        if len(hosts) == 1:
            raise httpx.RemoteProtocolError(
                "Server disconnected without sending a response.", request=request
            )
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": []})

    urls = ["https://primary.example"]
    if with_fallback:
        urls.append("https://fallback.example")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = BSCProvider(
            Settings(_env_file=None), client=client, rpc_urls=urls,
            max_retries=1, retry_backoff=0,
        )
        logs = await provider.get_logs(
            from_block=100, to_block=112, address="0xFactory", topics=("0xPairCreated",)
        )

    assert logs == []
    assert hosts == ["primary.example", "fallback.example" if with_fallback else "primary.example"]
    assert payloads[0] == payloads[1]
    assert payloads[1]["method"] == "eth_getLogs"


@pytest.mark.asyncio
async def test_bsc_provider_remote_eof_retries_are_bounded() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.RemoteProtocolError("Server disconnected", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = BSCProvider(
            Settings(_env_file=None, BSC_RPC_URL="https://bsc.example"), client=client,
            max_retries=2, retry_backoff=0,
        )
        with pytest.raises(DataUnavailable, match="eth_getLogs unavailable after 3 attempts") as error:
            await provider.get_logs(from_block=100, to_block=100)

    assert attempts == 3
    assert isinstance(error.value.__cause__, httpx.RemoteProtocolError)


@pytest.mark.asyncio
@pytest.mark.parametrize("http_status", [200, 403])
async def test_bsc_provider_does_not_retry_permanent_log_errors(http_status: int) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            http_status,
            json={"jsonrpc": "2.0", "id": 1,
                  "error": {"code": -32602, "message": "Archive access unavailable"}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = BSCProvider(
            Settings(_env_file=None), client=client,
            rpc_urls=("https://primary.example", "https://fallback.example"),
            max_retries=2, retry_backoff=0,
        )
        with pytest.raises(DataUnavailable, match="eth_getLogs failed permanently"):
            await provider.get_logs(from_block=100, to_block=100)

    assert attempts == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("batch_size", [4, 2])
async def test_bsc_provider_batches_log_addresses_without_losing_logs(batch_size: int) -> None:
    addresses = [f"0x{index:040x}" for index in range(11)]
    queries: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        query = json.loads(request.content)["params"][0]
        queries.append(query)
        if len(query["address"]) > batch_size:
            return httpx.Response(403, json={"error": {"code": -32602, "message": "Request blocked"}})
        start, end = int(query["fromBlock"], 16), int(query["toBlock"], 16)
        logs = [
            {"address": address, "topics": ["0xsync"], "data": "0x",
             "blockNumber": hex(end), "blockHash": f"0xblock{end}",
             "transactionHash": f"0xtx{end}-{addresses.index(address)}",
             "transactionIndex": "0x0", "logIndex": hex(addresses.index(address) + 1)}
            for address in reversed(query["address"])
        ]
        # Merge duplicate results even when a provider returns them in different batches.
        logs.append({
            "address": addresses[0], "topics": ["0xsync"], "data": "0x",
            "blockNumber": hex(start), "blockHash": f"0xblock{start}",
            "transactionHash": f"0xshared{start}", "transactionIndex": "0x0", "logIndex": "0x0",
        })
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": logs})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = BSCProvider(
            Settings(_env_file=None, BSC_RPC_URL="https://bsc.example",
                     BSC_LOG_ADDRESS_BATCH_SIZE=batch_size),
            client=client, log_chunk_size=2,
        )
        logs = await provider.get_logs(
            from_block=100, to_block=103, address=addresses, topics=(("0xswap", "0xsync"),)
        )

    for start, end in ((100, 101), (102, 103)):
        chunk_queries = [query for query in queries if query["fromBlock"] == hex(start)]
        assert [address for query in chunk_queries for address in query["address"]] == addresses
        assert all(query["toBlock"] == hex(end) for query in chunk_queries)
    assert all(0 < len(query["address"]) <= batch_size for query in queries)
    assert all(query["topics"] == [["0xswap", "0xsync"]] for query in queries)
    assert len(logs) == 24
    assert {log.address for log in logs} == set(addresses)
    assert [(log.block_number, log.log_index) for log in logs] == sorted(
        (log.block_number, log.log_index) for log in logs
    )
    assert len(provider.last_get_logs_metrics) == len(queries)
    assert [metric["address_count"] for metric in provider.last_get_logs_metrics] == [
        len(query["address"]) for query in queries
    ]
    assert [metric["start_block"] for metric in provider.last_get_logs_metrics] == [
        int(query["fromBlock"], 16) for query in queries
    ]


@pytest.mark.asyncio
async def test_bsc_provider_fails_closed_when_a_later_address_batch_is_rejected() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            return httpx.Response(403, json={"error": {"code": -32602, "message": "Access denied"}})
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = BSCProvider(
            Settings(_env_file=None, BSC_RPC_URL="https://bsc.example"), client=client,
        )
        with pytest.raises(DataUnavailable, match="eth_getLogs failed permanently"):
            await provider.get_logs(from_block=100, to_block=100, address=[f"0x{i}" for i in range(11)])

    assert attempts == 2

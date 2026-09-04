import json
from datetime import UTC, datetime

import httpx
import pytest

from smms.chains.bsc import BSCProvider
from smms.config import Settings


def _provider(handler, *, chunk_size: int = 10_000, retries: int = 2) -> tuple[BSCProvider, httpx.AsyncClient]:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = BSCProvider(
        Settings(_env_file=None, BSC_RPC_URL="https://bsc.example"),
        client=client,
        log_chunk_size=chunk_size,
        max_retries=retries,
        retry_backoff=0,
    )
    return provider, client


@pytest.mark.asyncio
async def test_bsc_provider_chunks_logs_and_deduplicates_boundary_results() -> None:
    ranges: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        request_filter = payload["params"][0]
        ranges.append((request_filter["fromBlock"], request_filter["toBlock"]))
        block = int(request_filter["fromBlock"], 16)
        logs = [
            {
                "address": "0xContract",
                "topics": ["0xTopic"],
                "data": "0x",
                "blockNumber": hex(block),
                "transactionHash": "0xTx",
                "logIndex": hex(block),
            }
        ]
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": logs})

    provider, client = _provider(handler, chunk_size=2)
    logs = await provider.get_logs(from_block=100, to_block=104)
    await client.aclose()

    assert ranges == [("0x64", "0x65"), ("0x66", "0x67"), ("0x68", "0x68")]
    assert [(log.block_number, log.log_index) for log in logs] == [
        (100, 100),
        (102, 102),
        (104, 104),
    ]


@pytest.mark.asyncio
async def test_bsc_provider_retries_transient_http_failures() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503, text="temporarily unavailable")
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": "0x123"})

    provider, client = _provider(handler, retries=2)
    assert await provider.get_latest_block_number() == 0x123
    await client.aclose()
    assert attempts == 2


@pytest.mark.asyncio
async def test_bsc_provider_rotates_configured_endpoints_without_logging_urls() -> None:
    endpoints: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        endpoints.append(str(request.url))
        if request.url.host == "one.example":
            return httpx.Response(503, text="down")
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": "0x38"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = BSCProvider(
        Settings(_env_file=None, BSC_RPC_URL="https://one.example"),
        client=client,
        rpc_urls=("https://one.example/api-key", "https://two.example/api-key"),
        max_retries=1,
        retry_backoff=0,
    )

    assert await provider.get_latest_block_number() == 56
    await client.aclose()

    assert endpoints == ["https://one.example/api-key", "https://two.example/api-key"]


@pytest.mark.asyncio
async def test_bsc_provider_exposes_eth_call_and_caches_block_timestamps() -> None:
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        methods.append(payload["method"])
        if payload["method"] == "eth_call":
            assert payload["params"] == [{"to": "0xCurve", "data": "0x1234"}, "0x7b"]
            result = "0xdeadbeef"
        else:
            result = {
                "number": "0x7b",
                "hash": "0xBlock",
                "timestamp": "0x65a00000",
                "transactions": [],
            }
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": result})

    provider, client = _provider(handler)
    assert await provider.eth_call("0xCurve", "0x1234", block_number=123) == "0xdeadbeef"
    first = await provider.get_block_timestamp(123)
    second = await provider.get_block_timestamp(123)
    await client.aclose()

    assert first == datetime.fromtimestamp(0x65A00000, tz=UTC)
    assert second == first
    assert methods == ["eth_call", "eth_getBlockByNumber"]

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx

from smms.chains.base import Block, ChainLog, ChainTransaction, TransactionReceipt
from smms.config import Settings
from smms.ingestion import DataUnavailable

logger = logging.getLogger(__name__)


class _RetryableRPCError(RuntimeError):
    """Internal marker for transport/server failures that are safe to retry."""


def _hex_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool):
        raise DataUnavailable(f"BSC RPC returned invalid {field}")
    try:
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            return int(value, 16) if value.lower().startswith("0x") else int(value)
    except (TypeError, ValueError):
        pass
    raise DataUnavailable(f"BSC RPC returned invalid {field}")


def _timestamp(value: Any) -> datetime:
    try:
        return datetime.fromtimestamp(_hex_int(value, field="timestamp"), tz=UTC)
    except (DataUnavailable, OverflowError, OSError) as exc:
        raise DataUnavailable("BSC RPC returned invalid block timestamp") from exc


class BSCProvider:
    """Read-only adapter for standard BNB Smart Chain JSON-RPC methods."""

    chain = "bsc"

    def __init__(
        self,
        settings: Settings,
        client: httpx.AsyncClient | None = None,
        *,
        rpc_urls: Sequence[str] | None = None,
        max_retries: int | None = None,
        retry_backoff: float | None = None,
        log_chunk_size: int | None = None,
        timeout: float | None = None,
    ) -> None:
        configured_urls = rpc_urls or settings.bsc_rpc_endpoints
        if not configured_urls:
            raise DataUnavailable("BSC_RPC_URL is not configured")
        active_max_retries = (
            settings.bsc_rpc_max_retries if max_retries is None else max_retries
        )
        active_retry_backoff = (
            settings.bsc_rpc_retry_backoff_seconds if retry_backoff is None else retry_backoff
        )
        active_chunk_size = settings.bsc_log_chunk_size if log_chunk_size is None else log_chunk_size
        active_timeout = settings.bsc_rpc_timeout_seconds if timeout is None else timeout
        if active_max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        if active_retry_backoff < 0:
            raise ValueError("retry_backoff must be non-negative")
        if active_chunk_size <= 0:
            raise ValueError("log_chunk_size must be positive")
        if active_timeout <= 0:
            raise ValueError("timeout must be positive")
        self.urls = tuple(configured_urls)
        self.url = self.urls[0]
        self.chain_id = settings.bsc_chain_id
        self.client = client
        self._owned_client = client is None
        self.max_retries = active_max_retries
        self.retry_backoff = active_retry_backoff
        self.log_chunk_size = active_chunk_size
        self.timeout = active_timeout
        self._block_timestamp_cache: dict[int, datetime] = {}

    async def _request(self, method: str, params: list[Any]) -> Any:
        client = self.client or httpx.AsyncClient(timeout=self.timeout)
        last_error: Exception | None = None
        try:
            for attempt in range(self.max_retries + 1):
                endpoint = self.urls[attempt % len(self.urls)]
                try:
                    response = await client.post(
                        endpoint,
                        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                    )
                    try:
                        response.raise_for_status()
                    except httpx.HTTPStatusError as exc:
                        if exc.response.status_code == 429 or exc.response.status_code >= 500:
                            raise _RetryableRPCError(str(exc)) from exc
                        raise DataUnavailable(f"BSC RPC {method} returned HTTP {exc.response.status_code}") from exc
                    payload = response.json()
                    if not isinstance(payload, dict):
                        raise DataUnavailable(f"BSC RPC {method} returned an invalid payload")
                    if payload.get("error"):
                        error = payload["error"]
                        code = error.get("code") if isinstance(error, dict) else None
                        if code in {-32005, 429}:
                            raise _RetryableRPCError(f"BSC RPC {method} failed: {error}")
                        raise DataUnavailable(f"BSC RPC {method} failed: {error}")
                    return payload.get("result")
                except _RetryableRPCError as exc:
                    last_error = exc
                    if attempt >= self.max_retries:
                        break
                    logger.warning(
                        "BSC RPC request failed; retrying method=%s attempt=%s endpoint_index=%s",
                        method,
                        attempt + 1,
                        attempt % len(self.urls),
                    )
                    if self.retry_backoff:
                        await asyncio.sleep(self.retry_backoff * (2**attempt))
                except (httpx.TimeoutException, httpx.NetworkError) as exc:
                    last_error = exc
                    if attempt >= self.max_retries:
                        break
                    if self.retry_backoff:
                        await asyncio.sleep(self.retry_backoff * (2**attempt))
                except (DataUnavailable, httpx.HTTPError, ValueError) as exc:
                    raise DataUnavailable(f"BSC RPC {method} failed permanently") from exc
            raise DataUnavailable(
                f"BSC RPC {method} unavailable after {self.max_retries + 1} attempts"
            ) from last_error
        finally:
            if self._owned_client:
                await client.aclose()

    @staticmethod
    def _block_tag(block_number: int) -> str:
        if block_number < 0:
            raise ValueError("block_number must be non-negative")
        return hex(block_number)

    @staticmethod
    def _normalize_log(item: dict[str, Any], *, fallback_block: int | None = None) -> ChainLog:
        address = item.get("address")
        transaction_hash = item.get("transactionHash")
        if not address or not transaction_hash:
            raise DataUnavailable("BSC log is missing address or transaction hash")
        block_value = item.get("blockNumber", fallback_block)
        log_index = item.get("logIndex")
        if block_value is None or log_index is None:
            raise DataUnavailable("BSC log is missing block or log index")
        topics = item.get("topics") or []
        if not isinstance(topics, list):
            raise DataUnavailable("BSC log topics are invalid")
        return ChainLog(
            address=str(address),
            topics=tuple(str(topic) for topic in topics),
            data=str(item.get("data", "0x")),
            block_number=_hex_int(block_value, field="blockNumber"),
            transaction_hash=str(transaction_hash),
            log_index=_hex_int(log_index, field="logIndex"),
            transaction_index=(
                _hex_int(item["transactionIndex"], field="transactionIndex")
                if item.get("transactionIndex") is not None
                else None
            ),
            raw=dict(item),
        )

    @classmethod
    def _normalize_transaction(
        cls,
        item: dict[str, Any],
        *,
        timestamp: datetime | None = None,
    ) -> ChainTransaction:
        transaction_hash = item.get("hash")
        from_address = item.get("from")
        if not transaction_hash or not from_address:
            raise DataUnavailable("BSC transaction is missing hash or sender")
        block_number = (
            _hex_int(item["blockNumber"], field="blockNumber")
            if item.get("blockNumber") is not None
            else None
        )
        value_raw = _hex_int(item.get("value", "0x0"), field="value")
        return ChainTransaction(
            transaction_hash=str(transaction_hash),
            block_number=block_number,
            from_address=str(from_address),
            to_address=str(item["to"]) if item.get("to") else None,
            value=Decimal(value_raw) / Decimal(10**18),
            input_data=str(item.get("input", "0x")),
            timestamp=timestamp,
            raw={**item, "value_wei": value_raw},
        )

    async def get_latest_block_number(self) -> int:
        result = await self._request("eth_blockNumber", [])
        return _hex_int(result, field="blockNumber")

    async def get_block(
        self, block_number: int, *, include_transactions: bool = False
    ) -> Block | None:
        result = await self._request(
            "eth_getBlockByNumber", [self._block_tag(block_number), include_transactions]
        )
        if result is None:
            return None
        if not isinstance(result, dict):
            raise DataUnavailable("BSC block payload is invalid")
        number = _hex_int(result.get("number", self._block_tag(block_number)), field="blockNumber")
        block_hash = result.get("hash")
        if not block_hash or result.get("timestamp") is None:
            raise DataUnavailable("BSC block is missing hash or timestamp")
        block_timestamp = _timestamp(result["timestamp"])
        self._block_timestamp_cache[number] = block_timestamp
        raw_transactions = result.get("transactions") or []
        transactions = tuple(
            self._normalize_transaction(item, timestamp=block_timestamp)
            for item in raw_transactions
            if isinstance(item, dict)
        )
        return Block(
            number=number,
            block_hash=str(block_hash),
            timestamp=block_timestamp,
            transactions=transactions,
            raw=dict(result),
        )

    async def get_block_timestamp(self, block_number: int) -> datetime | None:
        """Return a block timestamp, using one cached lookup per block."""

        if block_number < 0:
            raise ValueError("block_number must be non-negative")
        cached = self._block_timestamp_cache.get(block_number)
        if cached is not None:
            return cached
        block = await self.get_block(block_number, include_transactions=False)
        return block.timestamp if block is not None else None

    async def get_chain_id(self) -> int:
        """Read and validate the remote chain id before using the endpoint."""

        result = _hex_int(await self._request("eth_chainId", []), field="chainId")
        if result != self.chain_id:
            raise DataUnavailable(
                f"BSC RPC chain id mismatch: expected {self.chain_id}, received {result}"
            )
        return result

    async def eth_call(
        self,
        to_address: str,
        data: str = "0x",
        *,
        block_number: int | None = None,
    ) -> str:
        """Execute a read-only contract call at a historical block."""

        if not isinstance(to_address, str) or not to_address.strip():
            raise ValueError("to_address is required")
        if not isinstance(data, str) or not data.startswith(("0x", "0X")):
            raise ValueError("eth_call data must be a hex string")
        block_tag = "latest" if block_number is None else self._block_tag(block_number)
        result = await self._request(
            "eth_call",
            [{"to": to_address, "data": data}, block_tag],
        )
        if not isinstance(result, str):
            raise DataUnavailable("BSC eth_call returned an invalid result")
        return result

    async def get_code(self, address: str, *, block_number: int | None = None) -> str:
        """Return deployed bytecode for an address at a historical block.

        This is intentionally a thin read-only wrapper around ``eth_getCode``;
        callers must still decide whether the bytecode proves a particular
        contract role.
        """

        if not isinstance(address, str) or not address.strip():
            raise ValueError("address is required")
        block_tag = "latest" if block_number is None else self._block_tag(block_number)
        result = await self._request("eth_getCode", [address, block_tag])
        if not isinstance(result, str) or not result.startswith(("0x", "0X")):
            raise DataUnavailable("BSC eth_getCode returned an invalid result")
        return result

    async def get_transaction(self, transaction_hash: str) -> ChainTransaction | None:
        result = await self._request("eth_getTransactionByHash", [transaction_hash])
        if result is None:
            return None
        if not isinstance(result, dict):
            raise DataUnavailable("BSC transaction payload is invalid")
        block_number = (
            _hex_int(result["blockNumber"], field="blockNumber")
            if result.get("blockNumber") is not None
            else None
        )
        timestamp = self._block_timestamp_cache.get(block_number) if block_number is not None else None
        return self._normalize_transaction(result, timestamp=timestamp)

    async def get_transaction_receipt(self, transaction_hash: str) -> TransactionReceipt | None:
        result = await self._request("eth_getTransactionReceipt", [transaction_hash])
        if result is None:
            return None
        if not isinstance(result, dict) or result.get("blockNumber") is None:
            raise DataUnavailable("BSC receipt is missing block number")
        block_number = _hex_int(result["blockNumber"], field="blockNumber")
        raw_logs = result.get("logs") or []
        logs = tuple(
            self._normalize_log(item, fallback_block=block_number)
            for item in raw_logs
            if isinstance(item, dict)
        )
        cached_timestamp = self._block_timestamp_cache.get(block_number)
        if cached_timestamp is not None:
            logs = tuple(replace(log, timestamp=cached_timestamp) for log in logs)
        gas_used = (
            Decimal(_hex_int(result["gasUsed"], field="gasUsed"))
            if result.get("gasUsed") is not None
            else None
        )
        return TransactionReceipt(
            transaction_hash=str(result.get("transactionHash") or transaction_hash),
            block_number=block_number,
            status=(
                _hex_int(result["status"], field="status")
                if result.get("status") is not None
                else None
            ),
            logs=logs,
            gas_used=gas_used,
            contract_address=(
                str(result["contractAddress"]) if result.get("contractAddress") else None
            ),
            raw=dict(result),
        )

    async def get_logs(
        self,
        *,
        from_block: int,
        to_block: int,
        address: str | None = None,
        topics: Sequence[str] | None = None,
    ) -> list[ChainLog]:
        if from_block < 0 or to_block < from_block:
            raise ValueError("invalid BSC log block range")
        unique: dict[tuple[str, int], ChainLog] = {}
        chunk_start = from_block
        while chunk_start <= to_block:
            chunk_end = min(chunk_start + self.log_chunk_size - 1, to_block)
            request: dict[str, Any] = {
                "fromBlock": self._block_tag(chunk_start),
                "toBlock": self._block_tag(chunk_end),
            }
            if address:
                request["address"] = address
            if topics:
                request["topics"] = list(topics)
            result = await self._request("eth_getLogs", [request])
            if result is None:
                result = []
            if not isinstance(result, list):
                raise DataUnavailable("BSC logs payload is invalid")
            for item in result:
                if not isinstance(item, dict):
                    continue
                log = self._normalize_log(item)
                unique[(log.transaction_hash.lower(), log.log_index)] = log
            chunk_start = chunk_end + 1
        return sorted(unique.values(), key=lambda item: (item.block_number, item.log_index))


BscRPCProvider = BSCProvider
BscRpcProvider = BSCProvider
BSC_RPCProvider = BSCProvider

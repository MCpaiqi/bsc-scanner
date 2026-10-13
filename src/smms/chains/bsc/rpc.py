import asyncio
import logging
import time
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx

from smms.chains.base import Block, ChainLog, ChainTransaction, TransactionReceipt
from smms.chains.constants import BSC_CHAIN_ID
from smms.config import Settings
from smms.ingestion import DataUnavailable
from smms.observability import metrics, source_health_registry

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
        verify_on_use: bool = False,
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
        self.urls = tuple(str(url).strip() for url in configured_urls if str(url).strip())
        if not self.urls:
            raise DataUnavailable("BSC_RPC_URL is not configured")
        self.url = self.urls[0]
        self.active_endpoint_index = 0
        if settings.bsc_chain_id != BSC_CHAIN_ID:
            raise DataUnavailable(f"BSC provider requires chain id {BSC_CHAIN_ID}")
        self.chain_id = BSC_CHAIN_ID
        self.settings = settings
        self.client = client
        self._owned_client = client is None
        self.max_retries = active_max_retries
        self.retry_backoff = active_retry_backoff
        self.log_chunk_size = active_chunk_size
        self.log_address_batch_size = settings.bsc_log_address_batch_size
        self.timeout = active_timeout
        self.verify_on_use = verify_on_use
        self._verified = False
        self._probing = False
        self._usable_endpoint_indices: tuple[int, ...] = ()
        self._endpoint_health: dict[int, dict[str, Any]] = {
            index: {
                "endpoint_index": index,
                "configured": True,
                "usable": False,
                "transport": "http",
                "chain_verified": False,
                "degraded": False,
                "last_error": None,
            }
            for index in range(len(self.urls))
        }
        self._block_timestamp_cache: dict[int, datetime] = {}
        self._last_request_metric: dict[str, Any] | None = None
        self.last_request_metrics: tuple[dict[str, Any], ...] = ()
        self.last_get_logs_metrics: tuple[dict[str, Any], ...] = ()

    def _record_request_metric(self, metric: dict[str, Any]) -> None:
        """Retain a bounded, credential-free RPC timing trail for a chunk."""
        self._last_request_metric = metric
        self.last_request_metrics = (*self.last_request_metrics[-2_047:], metric)

    def reset_request_metrics(self) -> None:
        """Start a fresh catch-up telemetry window without changing RPC state."""
        self.last_request_metrics = ()

    def mark_endpoint_failure(
        self, error: Exception | str, *, endpoint_index: int | None = None
    ) -> None:
        """Isolate one endpoint without recording its credential-bearing URL."""

        index = self.active_endpoint_index if endpoint_index is None else endpoint_index
        state = self._endpoint_health.get(index)
        if state is not None:
            state.update(
                {
                    "usable": False,
                    "degraded": True,
                    "last_error": (
                        type(error).__name__
                        if isinstance(error, Exception)
                        else str(error)[:200]
                    ),
                }
            )
        if not self._probing:
            self._usable_endpoint_indices = tuple(
                item for item in self._usable_endpoint_indices if item != index
            )

    async def _request(
        self,
        method: str,
        params: list[Any],
        *,
        endpoint_indices: Sequence[int] | None = None,
    ) -> Any:
        client = self.client or httpx.AsyncClient(timeout=self.timeout)
        last_error: Exception | None = None
        try:
            candidates = tuple(
                endpoint_indices
                or self._usable_endpoint_indices
                or range(len(self.urls))
            )
            if not candidates:
                raise DataUnavailable("no usable BSC RPC endpoint")
            try:
                start = candidates.index(self.active_endpoint_index)
            except ValueError:
                start = 0
            for attempt in range(self.max_retries + 1):
                endpoint_index = candidates[(start + attempt) % len(candidates)]
                endpoint = self.urls[endpoint_index]
                self.active_endpoint_index = endpoint_index
                self.url = endpoint
                started = time.perf_counter()
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
                    result = payload.get("result")
                    latency_ms = (time.perf_counter() - started) * 1000
                    metrics.record_provider_request(
                        "bsc_http", latency_ms=latency_ms, success=True
                    )
                    source_health_registry.record_success("bsc_http", latency_ms=latency_ms)
                    state = self._endpoint_health.get(endpoint_index)
                    if state is not None:
                        state["last_error"] = None
                        state["degraded"] = False
                        if state.get("chain_verified"):
                            state["usable"] = True
                            if not self._probing and endpoint_index not in self._usable_endpoint_indices:
                                self._usable_endpoint_indices = tuple(
                                    sorted((*self._usable_endpoint_indices, endpoint_index))
                                )
                    if method == "eth_blockNumber":
                        try:
                            metrics.record_chain_head(_hex_int(result, field="blockNumber"))
                        except DataUnavailable:
                            pass
                    self._record_request_metric({
                        "method": method,
                        "endpoint_index": endpoint_index,
                        "retry_count": attempt,
                        "http_status": response.status_code,
                        "latency_ms": round(latency_ms, 2),
                        "outcome": "OK",
                    })
                    return result
                except _RetryableRPCError as exc:
                    last_error = exc
                    self.mark_endpoint_failure(exc, endpoint_index=endpoint_index)
                    latency_ms = (time.perf_counter() - started) * 1000
                    metrics.record_provider_request(
                        "bsc_http", latency_ms=latency_ms, success=False,
                        rate_limited="429" in str(exc),
                    )
                    source_health_registry.record_failure(
                        "bsc_http", exc, latency_ms=latency_ms, rate_limited="429" in str(exc)
                    )
                    self._record_request_metric({
                        "method": method,
                        "endpoint_index": endpoint_index,
                        "retry_count": attempt,
                        "http_status": getattr(getattr(exc, "__cause__", None), "response", None).status_code
                        if getattr(getattr(exc, "__cause__", None), "response", None) is not None else None,
                        "latency_ms": round(latency_ms, 2),
                        "outcome": "RATE_LIMITED" if "429" in str(exc) else "RETRYABLE_ERROR",
                    })
                    if attempt >= self.max_retries:
                        break
                    logger.warning(
                        "BSC RPC request failed; retrying method=%s attempt=%s endpoint_index=%s",
                        method,
                        attempt + 1,
                        endpoint_index,
                    )
                    if self.retry_backoff:
                        await asyncio.sleep(self.retry_backoff * (2**attempt))
                except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
                    last_error = exc
                    self.mark_endpoint_failure(exc, endpoint_index=endpoint_index)
                    latency_ms = (time.perf_counter() - started) * 1000
                    metrics.record_provider_request("bsc_http", latency_ms=latency_ms, success=False)
                    source_health_registry.record_failure("bsc_http", exc, latency_ms=latency_ms)
                    self._record_request_metric({
                        "method": method,
                        "endpoint_index": endpoint_index,
                        "retry_count": attempt,
                        "http_status": None,
                        "latency_ms": round(latency_ms, 2),
                        "outcome": "TIMEOUT" if isinstance(exc, httpx.TimeoutException) else "NETWORK_ERROR",
                    })
                    if attempt >= self.max_retries:
                        break
                    if self.retry_backoff:
                        await asyncio.sleep(self.retry_backoff * (2**attempt))
                except (DataUnavailable, httpx.HTTPError, ValueError) as exc:
                    self.mark_endpoint_failure(exc, endpoint_index=endpoint_index)
                    latency_ms = (time.perf_counter() - started) * 1000
                    metrics.record_provider_request("bsc_http", latency_ms=latency_ms, success=False)
                    source_health_registry.record_failure("bsc_http", exc, latency_ms=latency_ms)
                    self._record_request_metric({
                        "method": method,
                        "endpoint_index": endpoint_index,
                        "retry_count": attempt,
                        "http_status": None,
                        "latency_ms": round(latency_ms, 2),
                        "outcome": type(exc).__name__,
                    })
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
            block_hash=str(item["blockHash"]) if item.get("blockHash") else None,
            removed=bool(item.get("removed", False)),
            confirmation_status="pending",
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
        await self._ensure_verified()
        result = await self._request("eth_blockNumber", [])
        return _hex_int(result, field="blockNumber")

    async def get_block(
        self, block_number: int, *, include_transactions: bool = False
    ) -> Block | None:
        await self._ensure_verified()
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
        await self._ensure_verified()
        block = await self.get_block(block_number, include_transactions=False)
        return block.timestamp if block is not None else None

    async def get_chain_id(self) -> int:
        """Read and validate the remote chain id before using the endpoint."""

        result = _hex_int(await self._request("eth_chainId", []), field="chainId")
        if result != self.chain_id:
            self.mark_endpoint_failure("chain_id_mismatch")
            state = self._endpoint_health.get(self.active_endpoint_index)
            if state is not None:
                state["chain_verified"] = False
            raise DataUnavailable(
                f"BSC RPC chain id mismatch: expected {self.chain_id}, received {result}"
            )
        state = self._endpoint_health.get(self.active_endpoint_index)
        if state is not None:
            state["chain_verified"] = True
        return result

    async def probe_capabilities(self, *, endpoint_index: int | None = None) -> dict[str, Any]:
        """Verify chain identity and the minimal methods required for polling.

        The capability query is intentionally one block wide.  It proves that
        ``eth_getLogs`` is available without accidentally turning startup into
        a historical scan.
        """

        selected = (endpoint_index,) if endpoint_index is not None else None
        chain_id = _hex_int(
            await self._request("eth_chainId", [], endpoint_indices=selected), field="chainId"
        )
        if chain_id != BSC_CHAIN_ID:
            raise DataUnavailable(
                f"BSC RPC chain id mismatch: expected {BSC_CHAIN_ID}, received {chain_id}"
            )
        current_block = _hex_int(
            await self._request("eth_blockNumber", [], endpoint_indices=selected), field="blockNumber"
        )
        # Some public BSC RPCs return every log for an unfiltered block query;
        # that can exceed the response/timeout limit even though filtered log
        # ingestion is healthy. Probe the configured Pancake factory instead.
        factories = [
            item.strip()
            for item in str(getattr(self.settings, "bsc_dex_factory_addresses", "") or "").split(",")
            if item.strip()
        ]
        log_filter: dict[str, Any] = {
            "fromBlock": self._block_tag(current_block),
            "toBlock": self._block_tag(current_block),
        }
        if factories:
            log_filter["address"] = factories
        result = await self._request(
            "eth_getLogs",
            [log_filter],
            endpoint_indices=selected,
        )
        if result is None or not isinstance(result, list):
            raise DataUnavailable("BSC RPC eth_getLogs capability probe failed")
        return {
            "chain_id": chain_id,
            "current_block": current_block,
            "eth_blockNumber": True,
            "eth_getLogs": True,
        }

    async def probe_endpoints(self) -> tuple[dict[str, Any], ...]:
        """Probe every endpoint and retain only chain-verified log providers."""

        usable: list[int] = []
        results: list[dict[str, Any]] = []
        original_active = self.active_endpoint_index
        self._probing = True
        try:
            for endpoint_index, endpoint in enumerate(self.urls):
                self.active_endpoint_index = endpoint_index
                state = self._endpoint_health[endpoint_index]
                state.update(
                    {"usable": False, "chain_verified": False, "degraded": False, "last_error": None}
                )
                try:
                    result = await self.probe_capabilities(endpoint_index=endpoint_index)
                    state.update({"usable": True, "chain_verified": True, "degraded": False})
                    usable.append(endpoint_index)
                    results.append(
                        {"endpoint_index": endpoint_index, "status": "HEALTHY", **result}
                    )
                except Exception as exc:  # noqa: BLE001 - unhealthy endpoints are isolated
                    state.update(
                        {
                            "usable": False,
                            "chain_verified": False,
                            "degraded": True,
                            "last_error": type(exc).__name__,
                        }
                    )
                    results.append(
                        {
                            "endpoint_index": endpoint_index,
                            "status": "UNUSABLE",
                            "chain_verified": False,
                            "error": type(exc).__name__,
                        }
                    )
        finally:
            self._probing = False
            self._usable_endpoint_indices = tuple(usable)
            self._verified = bool(usable)
            if usable:
                self.active_endpoint_index = usable[0]
                self.url = self.urls[usable[0]]
            else:
                self.active_endpoint_index = original_active % len(self.urls)
                self.url = self.urls[self.active_endpoint_index]
        return tuple(results)

    async def ensure_usable(self) -> None:
        """Make startup fail closed when no configured endpoint is BSC-capable."""

        if self._verified and self._usable_endpoint_indices:
            return
        await self.probe_endpoints()
        if not self._usable_endpoint_indices:
            raise DataUnavailable("no usable BSC RPC endpoint passed chain/capability verification")

    def endpoint_status(self) -> tuple[dict[str, Any], ...]:
        """Return bounded, credential-free endpoint health metadata."""

        return tuple(dict(state) for state in self._endpoint_health.values())

    def status(self) -> dict[str, Any]:
        return {
            "transport": "http",
            "configured": bool(self.urls),
            "usable": bool(self._usable_endpoint_indices),
            "verified": self._verified,
            "active_endpoint_index": self.active_endpoint_index,
            "endpoints": list(self.endpoint_status()),
        }

    async def _ensure_verified(self) -> None:
        if not self.verify_on_use or self._probing:
            return
        await self.ensure_usable()

    check_capabilities = probe_capabilities

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
        await self._ensure_verified()
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
        await self._ensure_verified()
        result = await self._request("eth_getCode", [address, block_tag])
        if not isinstance(result, str) or not result.startswith(("0x", "0X")):
            raise DataUnavailable("BSC eth_getCode returned an invalid result")
        return result

    async def verify_factory(self, address: str) -> dict[str, Any]:
        """Verify that a configured BSC factory is deployed on chain 56."""
        code = await self.get_code(address)
        deployed = bool(code and code not in {"0x", "0x0"})
        return {"address": address.lower(), "deployed": deployed, "code_bytes": max(0, (len(code) - 2) // 2)}

    async def get_transaction(self, transaction_hash: str) -> ChainTransaction | None:
        await self._ensure_verified()
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
        await self._ensure_verified()
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
        address: str | Sequence[str] | None = None,
        topics: Sequence[Any] | None = None,
    ) -> list[ChainLog]:
        if from_block < 0 or to_block < from_block:
            raise ValueError("invalid BSC log block range")
        await self._ensure_verified()
        # Reorg replacements may reuse tx/log positions with a new block hash.
        unique: dict[tuple[str, int, str | None], ChainLog] = {}
        request_metrics: list[dict[str, Any]] = []
        if address is None or isinstance(address, str):
            address_batches: list[str | list[str] | None] = [address]
        else:
            addresses = list(address)
            address_batches = [
                addresses[offset:offset + self.log_address_batch_size]
                for offset in range(0, len(addresses), self.log_address_batch_size)
            ] or [None]
        chunk_start = from_block
        while chunk_start <= to_block:
            chunk_end = min(chunk_start + self.log_chunk_size - 1, to_block)
            for address_batch in address_batches:
                request: dict[str, Any] = {
                    "fromBlock": self._block_tag(chunk_start),
                    "toBlock": self._block_tag(chunk_end),
                }
                if address_batch:
                    request["address"] = address_batch
                if topics:
                    request["topics"] = list(topics)
                result = await self._request("eth_getLogs", [request])
                if self._last_request_metric is not None:
                    request_metrics.append({
                        **self._last_request_metric,
                        "start_block": chunk_start,
                        "end_block": chunk_end,
                        "address_count": len(address_batch)
                        if isinstance(address_batch, list) else int(bool(address_batch)),
                    })
                if result is None:
                    result = []
                if not isinstance(result, list):
                    raise DataUnavailable("BSC logs payload is invalid")
                for item in result:
                    if not isinstance(item, dict):
                        continue
                    log = self._normalize_log(item)
                    unique[(log.transaction_hash.lower(), log.log_index, log.block_hash)] = log
            chunk_start = chunk_end + 1
        self.last_get_logs_metrics = tuple(request_metrics)
        return sorted(unique.values(), key=lambda item: (item.block_number, item.log_index))


BscRPCProvider = BSCProvider
BscRpcProvider = BSCProvider
BSC_RPCProvider = BSCProvider

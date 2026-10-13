import asyncio
import json
import logging
import random
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from smms.chains.base import Block, ChainLog
from smms.chains.constants import BSC_CHAIN_ID
from smms.config import Settings
from smms.ingestion.provider import DataUnavailable
from smms.observability import metrics, source_health_registry

logger = logging.getLogger(__name__)


@dataclass
class StreamMetrics:
    current_block: int | None = None
    last_processed_block: int | None = None
    event_count: int = 0
    reconnect_count: int = 0
    provider_errors: int = 0
    decode_failures: int = 0
    ingestion_latency_ms: float | None = None
    last_event_at: datetime | None = None
    last_error: str | None = None
    event_times: deque[float] = field(default_factory=lambda: deque(maxlen=10_000), repr=False)

    @property
    def block_lag(self) -> int | None:
        if self.current_block is None or self.last_processed_block is None:
            return None
        return max(0, self.current_block - self.last_processed_block)

    @property
    def event_rate(self) -> float:
        now = time.monotonic()
        while self.event_times and self.event_times[0] < now - 60:
            self.event_times.popleft()
        return len(self.event_times) / 60

    def record_event(self, block_number: int | None = None) -> None:
        now = time.monotonic()
        self.event_count += 1
        self.event_times.append(now)
        self.last_event_at = datetime.now(UTC)
        if block_number is not None:
            self.current_block = max(self.current_block or 0, block_number)

    def record_decode_failure(self, error: Exception | str | None = None) -> None:
        self.decode_failures += 1
        self.provider_errors += 1
        metrics.increment("bsc_provider_errors_total")
        self.last_error = str(error)[:500] if error else "decode failure"
        metrics.record_ingestion_event(decoded=False)

    def record_provider_failure(self, error: Exception | str | None = None) -> None:
        self.provider_errors += 1
        metrics.increment("bsc_provider_errors_total")
        self.last_error = str(error)[:500] if error else "provider failure"


class HandoffBufferOverflow(RuntimeError):
    """Raised when a candidate WS cannot retain every handoff event."""


class BlockWindowDedupe:
    """Fork-aware identities retained for a configured block/reorg window."""

    def __init__(self, retention_blocks: int) -> None:
        self.retention_blocks = max(1, int(retention_blocks))
        self._blocks: dict[tuple[str, int, str | None], int] = {}
        self.head = 0
        self._pruned_through = -1

    def accept(self, log: ChainLog) -> bool:
        identity = (log.transaction_hash.lower(), log.log_index, log.block_hash)
        self.head = max(self.head, int(log.block_number))
        threshold = self.head - self.retention_blocks
        if threshold > self._pruned_through:
            self._blocks = {key: block for key, block in self._blocks.items() if block >= threshold}
            self._pruned_through = threshold
        if log.removed:
            self._blocks.pop(identity, None)
            return True
        if int(log.block_number) < threshold:
            return True
        if identity in self._blocks:
            return False
        self._blocks[identity] = int(log.block_number)
        return True

    def __len__(self) -> int:
        return len(self._blocks)


def _log_sort_key(log: ChainLog) -> tuple[int, int, int, str, bool]:
    return (
        log.block_number,
        log.transaction_index if log.transaction_index is not None else -1,
        log.log_index,
        log.transaction_hash.lower(),
        log.removed,
    )


_CANDIDATE_END = object()


class _WebsocketLogCandidate:
    """A subscribed WS connection whose events remain buffered until handoff."""

    def __init__(
        self, owner: "BSCWebsocketSubscription", websocket: Any, *, max_events: int
    ) -> None:
        self.owner = owner
        self.websocket = websocket
        self.endpoint_index = owner.active_endpoint_index
        self.queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=max_events)
        self.max_events = max_events
        self.error: Exception | None = None
        self.closed = False
        self._ended = False
        self._reader_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._reader_task = asyncio.create_task(self._read())

    async def _read(self) -> None:
        try:
            async for raw in self.websocket:
                payload = json.loads(raw) if isinstance(raw, str) else raw
                if not isinstance(payload, dict) or payload.get("method") != "eth_subscription":
                    continue
                try:
                    log = self.owner._normalize_log(payload.get("params", {}).get("result"))
                except DataUnavailable as exc:
                    self.owner.metrics.record_decode_failure(exc)
                    source_health_registry.record_failure("bsc_websocket", exc)
                    continue
                if self.queue.full():
                    self.error = HandoffBufferOverflow(
                        f"BSC websocket handoff buffer exceeded {self.max_events} events"
                    )
                    self.owner.mark_endpoint_failure(self.error, endpoint_index=self.endpoint_index)
                    break
                self.queue.put_nowait(log)
                self.owner.metrics.record_event(log.block_number)
                metrics.record_chain_head(log.block_number)
                metrics.record_ingestion_event()
                source_health_registry.record_success("bsc_websocket")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - surfaced to the handoff owner
            self.error = exc
            self.owner.mark_endpoint_failure(exc, endpoint_index=self.endpoint_index)
        finally:
            if not self.closed:
                try:
                    self.queue.put_nowait(_CANDIDATE_END)
                except asyncio.QueueFull:
                    # The overflow state is authoritative; do not drop a real
                    # event silently just to enqueue a sentinel.
                    if self.error is None:
                        self.error = HandoffBufferOverflow("BSC websocket handoff buffer is full")

    def __aiter__(self) -> "_WebsocketLogCandidate":
        return self

    async def __anext__(self) -> ChainLog:
        if self._ended and self.queue.empty():
            if self.error is not None:
                raise self.error
            raise StopAsyncIteration
        item = await self.queue.get()
        if item is _CANDIDATE_END:
            self._ended = True
            if self.error is not None:
                raise self.error
            raise StopAsyncIteration
        return item

    def drain_buffer(self) -> list[ChainLog]:
        """Atomically snapshot the currently buffered handoff events."""
        buffered: list[ChainLog] = []
        while not self.queue.empty():
            item = self.queue.get_nowait()
            if item is _CANDIDATE_END:
                self._ended = True
                break
            buffered.append(item)
        return buffered

    async def close(self) -> None:
        self.closed = True
        if self._reader_task is not None and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
        close = getattr(self.websocket, "close", None)
        if callable(close):
            result = close()
            if hasattr(result, "__await__"):
                await result


class BSCWebsocketSubscription:
    """Reconnectable, provider-independent ``eth_subscribe`` log stream."""

    provider = "bsc_websocket"

    def __init__(
        self,
        settings: Settings,
        *,
        connect: Callable[..., Any] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        random_fn: Callable[[], float] = random.random,
    ) -> None:
        if not settings.bsc_ws_endpoints:
            raise DataUnavailable("BSC_WS_URL is not configured")
        self.urls = settings.bsc_ws_endpoints
        self.url = self.urls[0]
        self.active_endpoint_index = 0
        self.settings = settings
        self._connect = connect
        self._sleep = sleep
        self._random = random_fn
        self.metrics = StreamMetrics()
        self._closed = False
        # A failover owner must take the HTTP path immediately after a
        # transport break so the durable cursor can replay the uncovered
        # window. Standalone WS consumers retain their bounded reconnect loop.
        self.failover_managed = False
        self.start_block: int | None = None
        self._request_id = 0
        self._endpoint_health: dict[int, dict[str, Any]] = {
            index: {
                "endpoint_index": index,
                "configured": True,
                "usable": False,
                "transport": "websocket",
                "chain_verified": False,
                "degraded": False,
                "last_error": None,
            }
            for index in range(len(self.urls))
        }

    async def close(self) -> None:
        self._closed = True

    def set_start_block(self, block_number: int | None) -> None:
        self.start_block = block_number

    def _backoff(self, attempt: int) -> float:
        base = min(
            self.settings.bsc_ws_backoff_max_seconds,
            self.settings.bsc_ws_backoff_seconds * (2**attempt),
        )
        return base + self.settings.bsc_ws_jitter_seconds * self._random()

    @staticmethod
    def _chain_id_matches(value: Any) -> bool | None:
        """Return True/False for a parseable chain id, None for old test doubles."""

        if isinstance(value, bool) or value is None:
            return None
        try:
            if isinstance(value, int):
                return value == BSC_CHAIN_ID
            text = str(value).strip()
            if not text:
                return None
            return (int(text, 16) if text.lower().startswith("0x") else int(text)) == BSC_CHAIN_ID
        except (TypeError, ValueError):
            return None

    async def _connection(self, endpoint_index: int | None = None):
        if endpoint_index is not None:
            self.active_endpoint_index = endpoint_index % len(self.urls)
        url = self.urls[self.active_endpoint_index % len(self.urls)]
        self.url = url
        if self._connect is not None:
            result = self._connect(
                url,
                open_timeout=self.settings.bsc_ws_connect_timeout_seconds,
                ping_interval=self.settings.bsc_ws_heartbeat_seconds,
            )
            return await result if hasattr(result, "__await__") else result
        try:
            import websockets

            return await websockets.connect(
                url,
                open_timeout=self.settings.bsc_ws_connect_timeout_seconds,
                ping_interval=self.settings.bsc_ws_heartbeat_seconds,
            )
        except Exception as exc:
            raise DataUnavailable("BSC WebSocket connection failed") from exc

    async def _handshake_logs(
        self,
        websocket: Any,
        *,
        address: str | Sequence[str] | None,
        topics: Sequence[str] | None,
    ) -> str:
        self._request_id += 1
        chain_request_id = self._request_id
        await websocket.send(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": chain_request_id,
                    "method": "eth_chainId",
                    "params": [],
                }
            )
        )
        chain_reply = json.loads(await websocket.recv())
        chain_result = chain_reply.get("result") if isinstance(chain_reply, dict) else None
        chain_match = self._chain_id_matches(chain_result)
        if chain_match is not True:
            raise DataUnavailable("BSC websocket chain id mismatch or missing")
        state = self._endpoint_health.get(self.active_endpoint_index)
        if state is not None:
            state["chain_verified"] = chain_match is True
        self._request_id += 1
        subscription_request_id = self._request_id
        await websocket.send(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": subscription_request_id,
                    "method": "eth_subscribe",
                    "params": ["logs", self._filter(address, topics)],
                }
            )
        )
        reply = json.loads(await websocket.recv())
        subscription_id = reply.get("result") if isinstance(reply, dict) else None
        if not subscription_id:
            raise DataUnavailable("BSC websocket subscription was rejected")
        if state is not None:
            state.update({"usable": True, "degraded": False, "last_error": None})
        return str(subscription_id)

    async def open_log_candidate(
        self,
        *,
        address: str | None = None,
        topics: Sequence[str] | None = None,
    ) -> _WebsocketLogCandidate:
        """Open and subscribe a bounded candidate connection for failback."""

        websocket = await self._connection()
        try:
            await self._handshake_logs(websocket, address=address, topics=topics)
            candidate = _WebsocketLogCandidate(
                self,
                websocket,
                max_events=self.settings.bsc_ws_handoff_buffer_max_events,
            )
            await candidate.start()
            return candidate
        except Exception:
            state = self._endpoint_health.get(self.active_endpoint_index)
            if state is not None:
                state.update(
                    {
                        "usable": False,
                        "chain_verified": False,
                        "degraded": True,
                        "last_error": "chain_verification_failed",
                    }
                )
            close = getattr(websocket, "close", None)
            if callable(close):
                result = close()
                if hasattr(result, "__await__"):
                    await result
            raise

    def endpoint_status(self) -> tuple[dict[str, Any], ...]:
        return tuple(dict(item) for item in self._endpoint_health.values())

    def mark_endpoint_failure(
        self, error: Exception | str, *, endpoint_index: int | None = None
    ) -> None:
        """Mark a transport endpoint unusable after a candidate failure."""

        index = self.active_endpoint_index if endpoint_index is None else endpoint_index
        state = self._endpoint_health.get(index)
        if state is None:
            return
        state.update(
            {
                "usable": False,
                "chain_verified": False,
                "degraded": True,
                "last_error": type(error).__name__
                if isinstance(error, Exception)
                else str(error)[:200],
            }
        )

    def status(self) -> dict[str, Any]:
        return {
            "transport": "websocket",
            "configured": bool(self.urls),
            "usable": any(item["usable"] for item in self._endpoint_health.values()),
            "active_endpoint_index": self.active_endpoint_index,
            "endpoints": list(self.endpoint_status()),
        }

    @staticmethod
    def _filter(address: str | Sequence[str] | None, topics: Sequence[Any] | None) -> dict[str, Any]:
        value: dict[str, Any] = {}
        if address:
            value["address"] = list(address) if not isinstance(address, str) else address
        if topics:
            value["topics"] = list(topics)
        return value

    @staticmethod
    def _normalize_log(item: Any) -> ChainLog:
        if not isinstance(item, dict) or any(
            not item.get(k) for k in ("address", "transactionHash", "blockNumber", "logIndex")
        ):
            raise DataUnavailable("BSC websocket log is malformed")
        try:
            block = int(str(item["blockNumber"]), 16)
            index = int(str(item["logIndex"]), 16)
            tx_index = (
                int(str(item["transactionIndex"]), 16)
                if item.get("transactionIndex") is not None
                else None
            )
        except (TypeError, ValueError) as exc:
            raise DataUnavailable("BSC websocket log has invalid numeric fields") from exc
        topics = item.get("topics") or []
        if not isinstance(topics, list):
            raise DataUnavailable("BSC websocket log topics are invalid")
        return ChainLog(
            address=str(item["address"]),
            topics=tuple(str(t) for t in topics),
            data=str(item.get("data", "0x")),
            block_number=block,
            transaction_hash=str(item["transactionHash"]),
            log_index=index,
            transaction_index=tx_index,
            raw=dict(item),
            block_hash=str(item["blockHash"]) if item.get("blockHash") else None,
            removed=bool(item.get("removed", False)),
            confirmation_status="orphaned" if item.get("removed") else "pending",
        )

    async def subscribe_logs(
        self, *, address: str | Sequence[str] | None = None, topics: Sequence[Any] | None = None
    ) -> AsyncIterator[ChainLog]:
        # A reorg replacement can reuse the transaction/log position while
        # carrying a different block hash; keep that replacement observable.
        retention = (
            self.settings.bsc_confirmation_depth
            + self.settings.bsc_reorg_lookback_blocks
            + self.settings.bsc_reorg_lookback_blocks
        )
        seen = BlockWindowDedupe(retention)
        attempt = 0
        while not self._closed:
            websocket = None
            try:
                websocket = await self._connection()
                await websocket.send(
                    json.dumps({"jsonrpc": "2.0", "id": 0, "method": "eth_chainId", "params": []})
                )
                chain_reply = json.loads(await websocket.recv())
                chain_result = chain_reply.get("result") if isinstance(chain_reply, dict) else None
                chain_match = self._chain_id_matches(chain_result)
                if chain_match is not True:
                    raise DataUnavailable("BSC websocket chain id mismatch")
                await websocket.send(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "eth_subscribe",
                            "params": ["logs", self._filter(address, topics)],
                        }
                    )
                )
                reply = json.loads(await websocket.recv())
                if not isinstance(reply, dict) or not reply.get("result"):
                    raise DataUnavailable("BSC websocket subscription was rejected")
                state = self._endpoint_health.get(self.active_endpoint_index)
                if state is not None:
                    state.update(
                        {
                            "usable": True,
                            "chain_verified": chain_match is True,
                            "degraded": False,
                            "last_error": None,
                        }
                    )
                attempt = 0
                async for raw in websocket:
                    payload = json.loads(raw) if isinstance(raw, str) else raw
                    if not isinstance(payload, dict) or payload.get("method") != "eth_subscription":
                        continue
                    try:
                        log = self._normalize_log(payload.get("params", {}).get("result"))
                    except DataUnavailable as exc:
                        self.metrics.record_decode_failure(exc)
                        source_health_registry.record_failure("bsc_websocket", exc)
                        continue
                    if not seen.accept(log):
                        continue
                    self.metrics.record_event(log.block_number)
                    metrics.record_chain_head(log.block_number)
                    metrics.record_ingestion_event()
                    source_health_registry.record_success("bsc_websocket")
                    yield log
                if not self._closed:
                    raise ConnectionError("BSC websocket connection closed")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self._closed:
                    break
                self.metrics.reconnect_count += 1
                metrics.increment("bsc_ws_reconnect_total")
                self.metrics.record_provider_failure(exc)
                source_health_registry.record_failure("bsc_websocket", exc)
                state = self._endpoint_health.get(self.active_endpoint_index)
                if state is not None:
                    state.update(
                        {"usable": False, "degraded": True, "last_error": type(exc).__name__}
                    )
                if self.failover_managed:
                    raise DataUnavailable(
                        "BSC websocket transport failed; HTTP failover required"
                    ) from exc
                limit = self.settings.bsc_ws_reconnect_max_retries
                if limit >= 0 and attempt >= limit:
                    raise DataUnavailable("BSC websocket reconnect limit exceeded") from exc
                self.active_endpoint_index = (self.active_endpoint_index + 1) % len(self.urls)
                await self._sleep(self._backoff(attempt))
                attempt += 1
            finally:
                if websocket is not None and hasattr(websocket, "close"):
                    result = websocket.close()
                    if hasattr(result, "__await__"):
                        await result

    async def subscribe_blocks(self) -> AsyncIterator[Block]:
        attempt = 0
        while not self._closed:
            websocket = None
            try:
                websocket = await self._connection()
                await websocket.send(
                    json.dumps({"jsonrpc": "2.0", "id": 0, "method": "eth_chainId", "params": []})
                )
                chain_reply = json.loads(await websocket.recv())
                chain_result = chain_reply.get("result") if isinstance(chain_reply, dict) else None
                chain_match = self._chain_id_matches(chain_result)
                if chain_match is not True:
                    raise DataUnavailable("BSC websocket chain id mismatch")
                await websocket.send(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "eth_subscribe",
                            "params": ["newHeads"],
                        }
                    )
                )
                reply = json.loads(await websocket.recv())
                if not isinstance(reply, dict) or not reply.get("result"):
                    raise DataUnavailable("BSC websocket block subscription was rejected")
                state = self._endpoint_health.get(self.active_endpoint_index)
                if state is not None:
                    state.update(
                        {
                            "usable": True,
                            "chain_verified": chain_match is True,
                            "degraded": False,
                            "last_error": None,
                        }
                    )
                attempt = 0
                async for raw in websocket:
                    payload = json.loads(raw) if isinstance(raw, str) else raw
                    if not isinstance(payload, dict) or payload.get("method") != "eth_subscription":
                        continue
                    result = payload.get("params", {}).get("result")
                    if not isinstance(result, dict):
                        self.metrics.record_decode_failure("invalid websocket block payload")
                        source_health_registry.record_failure(
                            "bsc_websocket", "invalid block payload"
                        )
                        continue
                    try:
                        number = int(str(result["number"]), 16)
                        timestamp = datetime.fromtimestamp(
                            int(str(result["timestamp"]), 16), tz=UTC
                        )
                        block_hash = str(result["hash"])
                    except (KeyError, TypeError, ValueError, OverflowError, OSError):
                        self.metrics.record_decode_failure("invalid websocket block header")
                        source_health_registry.record_failure(
                            "bsc_websocket", "invalid block header"
                        )
                        continue
                    self.metrics.current_block = number
                    metrics.record_chain_head(number)
                    source_health_registry.record_success("bsc_websocket")
                    yield Block(
                        number=number, block_hash=block_hash, timestamp=timestamp, raw=dict(result)
                    )
                if not self._closed:
                    raise ConnectionError("BSC websocket connection closed")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self._closed:
                    break
                self.metrics.reconnect_count += 1
                metrics.increment("bsc_ws_reconnect_total")
                self.metrics.record_provider_failure(exc)
                source_health_registry.record_failure("bsc_websocket", exc)
                state = self._endpoint_health.get(self.active_endpoint_index)
                if state is not None:
                    state.update(
                        {"usable": False, "degraded": True, "last_error": type(exc).__name__}
                    )
                limit = self.settings.bsc_ws_reconnect_max_retries
                if limit >= 0 and attempt >= limit:
                    raise DataUnavailable("BSC websocket reconnect limit exceeded") from exc
                self.active_endpoint_index = (self.active_endpoint_index + 1) % len(self.urls)
                await self._sleep(self._backoff(attempt))
                attempt += 1
            finally:
                if websocket is not None and hasattr(websocket, "close"):
                    result = websocket.close()
                    if hasattr(result, "__await__"):
                        await result

    async def probe_smoke(self, *, factory_address: str | None = None) -> dict[str, Any]:
        """Verify chain 56 and a short-lived factory logs subscription."""
        factories = [
            item.strip()
            for item in str(self.settings.bsc_dex_factory_addresses or "").split(",")
            if item.strip()
        ]
        factory = factory_address or (factories[0] if factories else None)
        if not factory:
            return {
                "ws_connect": "SKIPPED",
                "ws_chain_id": None,
                "ws_logs_subscription": "SKIPPED",
                "ws_unsubscribe": "SKIPPED",
                "latency_ms": 0.0,
                "reason": "FACTORY_NOT_CONFIGURED",
                "ready": False,
            }
        original_index = self.active_endpoint_index
        started = time.perf_counter()
        last_reason = "WS_CONNECTION_FAILED"
        last_result: dict[str, Any] = {
            "ws_connect": "FAILED",
            "ws_chain_id": "SKIPPED",
            "ws_logs_subscription": "SKIPPED",
            "ws_unsubscribe": "SKIPPED",
        }
        for endpoint_index in range(len(self.urls)):
            websocket = None
            self.active_endpoint_index = endpoint_index
            attempt_result = {
                "ws_connect": "FAILED",
                "ws_chain_id": "SKIPPED",
                "ws_logs_subscription": "SKIPPED",
                "ws_unsubscribe": "SKIPPED",
            }
            try:
                last_reason = "WS_CONNECTION_FAILED"
                websocket = await self._connection()
                attempt_result["ws_connect"] = "OK"
                from smms.market.protocols import PancakeSwapConfig

                last_reason = "WS_CHAIN_ID_UNUSABLE"
                attempt_result["ws_chain_id"] = "FAILED"
                self._request_id += 1
                await websocket.send(json.dumps({
                    "jsonrpc": "2.0", "id": self._request_id,
                    "method": "eth_chainId", "params": [],
                }))
                chain_reply = json.loads(await websocket.recv())
                chain_result = chain_reply.get("result") if isinstance(chain_reply, dict) else None
                if self._chain_id_matches(chain_result) is not True:
                    raise DataUnavailable("BSC websocket chain id mismatch or missing")
                attempt_result["ws_chain_id"] = BSC_CHAIN_ID

                last_reason = "WS_LOGS_SUBSCRIPTION_UNUSABLE"
                attempt_result["ws_logs_subscription"] = "FAILED"
                self._request_id += 1
                await websocket.send(json.dumps({
                    "jsonrpc": "2.0", "id": self._request_id,
                    "method": "eth_subscribe",
                    "params": [
                        "logs",
                        self._filter(factory, (PancakeSwapConfig().pair_created_topic,)),
                    ],
                }))
                subscription_reply = json.loads(await websocket.recv())
                subscription_id = (
                    subscription_reply.get("result")
                    if isinstance(subscription_reply, dict)
                    else None
                )
                if not subscription_id:
                    raise DataUnavailable("BSC websocket subscription was rejected")
                attempt_result["ws_logs_subscription"] = "OK"

                last_reason = "WS_UNSUBSCRIBE_UNUSABLE"
                attempt_result["ws_unsubscribe"] = "FAILED"
                self._request_id += 1
                await websocket.send(json.dumps({
                    "jsonrpc": "2.0",
                    "id": self._request_id,
                    "method": "eth_unsubscribe",
                    "params": [subscription_id],
                }))
                unsubscribe_reply = json.loads(await websocket.recv())
                if not isinstance(unsubscribe_reply, dict) or not unsubscribe_reply.get("result"):
                    raise DataUnavailable("BSC websocket unsubscribe was rejected")
                attempt_result["ws_unsubscribe"] = "OK"
                state = self._endpoint_health.get(endpoint_index)
                if state is not None:
                    state.update(
                        {
                            "usable": True,
                            "chain_verified": True,
                            "degraded": False,
                            "last_error": None,
                        }
                    )
                source_health_registry.record_success(
                    "bsc_websocket", details={"provider_index": endpoint_index}
                )
                if self._endpoint_health.get(endpoint_index, {}).get("usable"):
                    return {
                        **attempt_result,
                        "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                        "reason": None,
                        "ready": True,
                    }
            except Exception as exc:  # noqa: BLE001 - smoke must report every endpoint failure
                last_result = attempt_result
                state = self._endpoint_health.get(endpoint_index)
                if state is not None:
                    state.update(
                        {
                            "usable": False,
                            "chain_verified": False,
                            "degraded": True,
                            "last_error": type(exc).__name__,
                        }
                    )
            finally:
                if websocket is not None and hasattr(websocket, "close"):
                    close_result = websocket.close()
                    if hasattr(close_result, "__await__"):
                        await close_result
        self.active_endpoint_index = original_index
        return {
            **last_result,
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            "reason": last_reason,
            "ready": False,
        }

    async def probe_health(self) -> bool:
        """Return whether the full short-lived factory subscription succeeds."""
        return bool((await self.probe_smoke()).get("ready"))


class BSCHTTPPollingSubscription:
    """Explicit HTTP fallback; this is never reported as a websocket."""

    provider = "bsc_http_polling"

    def __init__(
        self,
        provider: Any,
        settings: Settings,
        *,
        start_block: int | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.rpc_provider = provider
        self.settings = settings
        self.metrics = StreamMetrics()
        self._closed = False
        self.start_block = start_block
        self._sleep = sleep

    def set_start_block(self, block_number: int | None) -> None:
        self.start_block = block_number

    async def close(self) -> None:
        self._closed = True

    async def subscribe_logs(
        self, *, address: str | Sequence[str] | None = None, topics: Sequence[str] | None = None
    ) -> AsyncIterator[ChainLog]:
        if address is None and not topics:
            from smms.market.protocols import PancakeSwapConfig
            topics = (PancakeSwapConfig().pair_created_topic,)
        last_block = self.start_block
        retention = (
            int(self.settings.bsc_confirmation_depth)
            + int(self.settings.bsc_reorg_lookback_blocks)
            + int(self.settings.bsc_reorg_lookback_blocks)
        )
        seen = BlockWindowDedupe(retention)
        while not self._closed:
            try:
                started = time.perf_counter()
                latest = await self.rpc_provider.get_latest_block_number()
                self.metrics.current_block = latest
                metrics.record_chain_head(latest)
                source_health_registry.record_success(
                    "bsc_http", latency_ms=(time.perf_counter() - started) * 1000
                )
                if last_block is None:
                    last_block = latest - 1
                scan_from = max(0, latest - int(self.settings.bsc_reorg_lookback_blocks))
                if last_block is not None:
                    scan_from = min(scan_from, last_block + 1)
                if last_block is None or latest >= scan_from:
                    logs = await self.rpc_provider.get_logs(
                        from_block=scan_from,
                        to_block=latest,
                        address=address,
                        topics=topics,
                    )
                    for log in logs:
                        if not seen.accept(log):
                            continue
                        self.metrics.record_event(log.block_number)
                        metrics.record_ingestion_event()
                        source_health_registry.record_success("bsc_http")
                        yield log
                    last_block = latest
                await self._sleep(self.settings.bsc_poll_interval_seconds)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - polling must survive provider outages
                self.metrics.record_provider_failure(exc)
                source_health_registry.record_failure("bsc_http", exc)
                await self._sleep(self.settings.bsc_poll_interval_seconds)


class BSCFailoverSubscription:
    """Bridge WS and HTTP without exposing a handoff gap to callers.

    A failback always follows the same ordering: candidate WS connection and
    subscription, bounded buffering, a block barrier, HTTP replay from the
    durable cursor/reorg window, deterministic drain, then WS primary.  The
    class deliberately does not invent a cursor from transport-local metrics;
    ``observe_checkpoint`` is fed by the durable ingestion service.
    """

    provider = "bsc_websocket"

    def __init__(
        self,
        websocket: BSCWebsocketSubscription,
        http: BSCHTTPPollingSubscription,
        *,
        probe_seconds: float | None = None,
        required_successes: int | None = None,
        handoff_buffer_max_events: int | None = None,
        reorg_lookback_blocks: int | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.websocket = websocket
        self.http = http
        self.active_provider = websocket.provider
        self.metrics = websocket.metrics
        self._closed = False
        self.websocket.failover_managed = True
        self.failover_count = 0
        self.failback_count = 0
        self.active_transport = "ws"
        self._probe_seconds = (
            probe_seconds
            if probe_seconds is not None
            else websocket.settings.bsc_ws_failback_probe_seconds
        )
        self._required_successes = (
            required_successes
            if required_successes is not None
            else websocket.settings.bsc_ws_failback_required_successes
        )
        self._handoff_buffer_max_events = (
            handoff_buffer_max_events
            if handoff_buffer_max_events is not None
            else websocket.settings.bsc_ws_handoff_buffer_max_events
        )
        self._reorg_lookback_blocks = (
            reorg_lookback_blocks
            if reorg_lookback_blocks is not None
            else websocket.settings.bsc_reorg_lookback_blocks
        )
        self._sleep = sleep
        retention = (
            websocket.settings.bsc_confirmation_depth
            + websocket.settings.bsc_reorg_lookback_blocks
            + websocket.settings.bsc_reorg_lookback_blocks
        )
        self._seen = BlockWindowDedupe(retention)
        self._prepared_candidate: _WebsocketLogCandidate | None = None
        self._committed_candidate: _WebsocketLogCandidate | None = None
        self._durable_block: int | None = None
        self._durable_log: int | None = None
        self._durable_transaction: str | None = None

    def set_start_block(self, block_number: int | None) -> None:
        setter = getattr(self.websocket, "set_start_block", None)
        if callable(setter):
            setter(block_number)
        self.http.set_start_block(block_number)
        self._durable_block = block_number
        self._durable_log = None
        self._durable_transaction = None

    def observe_checkpoint(
        self,
        block_number: int | None,
        log_index: int | None = None,
        transaction_hash: str | None = None,
    ) -> None:
        """Update the local handoff hint from a committed durable checkpoint."""

        if block_number is None:
            return
        if self._durable_block is None or block_number > self._durable_block:
            self._durable_block = block_number
            self._durable_log = log_index
            self._durable_transaction = transaction_hash
        elif block_number == self._durable_block and (
            self._durable_log is None or (log_index is not None and log_index >= self._durable_log)
        ):
            self._durable_log = log_index
            self._durable_transaction = transaction_hash

    def _accept(self, log: ChainLog) -> bool:
        return self._seen.accept(log)

    async def open_log_candidate(
        self, *, address: Any = None, topics: Sequence[str] | None = None
    ) -> _WebsocketLogCandidate:
        """Expose the same ACKed candidate primitive for startup handoff."""
        candidate = await self.prepare_log_candidate(address=address, topics=topics)
        if candidate is None:
            raise DataUnavailable("BSC websocket candidate factory is unavailable")
        return candidate

    async def prepare_log_candidate(
        self, *, address: Any = None, topics: Sequence[str] | None = None
    ) -> _WebsocketLogCandidate | None:
        """Prepare an ACKed candidate while retaining failover ownership."""
        candidate = await self._open_candidate(address=address, topics=topics)
        self._prepared_candidate = candidate
        return candidate

    def commit_prepared_candidate(self, candidate: _WebsocketLogCandidate | None = None) -> Any:
        """Commit a prepared candidate to this failover owner's primary lane."""
        selected = candidate or self._prepared_candidate
        if selected is None:
            raise DataUnavailable("BSC websocket prepared candidate is unavailable")
        self._prepared_candidate = None
        self._committed_candidate = selected
        return selected

    def _http_replay_start(self, reference_block: int | None = None) -> int:
        if self._durable_block is None:
            if reference_block is None:
                return 0
            return max(0, reference_block - max(0, self._reorg_lookback_blocks))
        return max(0, self._durable_block - max(0, self._reorg_lookback_blocks))

    def _sync_http_metrics(self) -> None:
        self.metrics.current_block = self.http.metrics.current_block
        self.metrics.event_count = max(self.metrics.event_count, self.http.metrics.event_count)
        self.metrics.reconnect_count = max(
            self.metrics.reconnect_count, self.http.metrics.reconnect_count
        )
        self.metrics.provider_errors = max(
            self.metrics.provider_errors, self.http.metrics.provider_errors
        )
        self.metrics.decode_failures = max(
            self.metrics.decode_failures, self.http.metrics.decode_failures
        )
        self.metrics.ingestion_latency_ms = self.http.metrics.ingestion_latency_ms
        self.metrics.last_event_at = self.http.metrics.last_event_at
        if self.metrics.current_block is not None:
            metrics.record_chain_head(self.metrics.current_block)

    async def _open_candidate(
        self,
        *,
        address: str | Sequence[str] | None,
        topics: Sequence[str] | None,
    ) -> _WebsocketLogCandidate | None:
        opener = getattr(self.websocket, "open_log_candidate", None)
        if not callable(opener):
            return None
        candidate = await opener(address=address, topics=topics)
        if not isinstance(candidate, _WebsocketLogCandidate) and not hasattr(candidate, "queue"):
            raise DataUnavailable("BSC websocket candidate has no bounded handoff buffer")
        return candidate

    async def close(self) -> None:
        self._closed = True
        candidates = {
            candidate
            for candidate in (self._prepared_candidate, self._committed_candidate)
            if candidate is not None
        }
        self._prepared_candidate = None
        self._committed_candidate = None
        for candidate in candidates:
            await candidate.close()
        await self.websocket.close()
        await self.http.close()

    async def subscribe_logs(
        self, *, address: str | Sequence[str] | None = None, topics: Sequence[str] | None = None
    ) -> AsyncIterator[ChainLog]:
        if address is None and not topics:
            from smms.market.protocols import PancakeSwapConfig
            topics = (PancakeSwapConfig().pair_created_topic,)
        initial_candidate = self._committed_candidate
        self._committed_candidate = None
        while not self._closed:
            if initial_candidate is None and self._committed_candidate is not None:
                initial_candidate = self._committed_candidate
                self._committed_candidate = None
            active_candidate: _WebsocketLogCandidate | None = None
            try:
                if initial_candidate is not None:
                    active_candidate = initial_candidate
                    initial_candidate = None
                    async for log in active_candidate:
                        self.active_provider = self.websocket.provider
                        self.active_transport = "ws"
                        if self._accept(log):
                            yield log
                    raise ConnectionError("BSC websocket committed candidate ended")
                async for log in self.websocket.subscribe_logs(address=address, topics=topics):
                    self.active_provider = self.websocket.provider
                    self.active_transport = "ws"
                    if self._accept(log):
                        yield log
                if self._closed:
                    return
                raise ConnectionError("BSC websocket stream ended")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - fail over without losing the chain lane
                if active_candidate is not None:
                    mark_failure = getattr(self.websocket, "mark_endpoint_failure", None)
                    if callable(mark_failure):
                        mark_failure(
                            exc,
                            endpoint_index=getattr(active_candidate, "endpoint_index", None),
                        )
                    await active_candidate.close()
                self.active_provider = self.http.provider
                self.active_transport = "http"
                self.failover_count += 1
                metrics.increment("bsc_provider_failover_total")
                source_health_registry.record_failure("bsc_websocket", exc)
            if self._closed:
                return
            # HTTP is primary after a WS outage.  Its cursor starts from the
            # durable checkpoint minus a finite reorg lookback; replay is safe
            # because the ingestion repository owns idempotency.
            self.http.set_start_block(self._http_replay_start())
            source_health_registry.set_status(
                "bsc_websocket", "DEGRADED", details={"fallback": "bsc_http"}
            )
            successes = 0
            last_probe = time.monotonic() - self._probe_seconds
            http_iterator = self.http.subscribe_logs(address=address, topics=topics).__aiter__()
            pending_http: asyncio.Task[ChainLog] | None = asyncio.create_task(
                http_iterator.__anext__()
            )
            candidate_ready: _WebsocketLogCandidate | None = None
            try:
                while not self._closed:
                    now = time.monotonic()
                    probe_due = now - last_probe >= self._probe_seconds
                    if probe_due:
                        last_probe = now
                        probe = getattr(self.websocket, "probe_health", None)
                        healthy = await probe() if callable(probe) else False
                        successes = successes + 1 if healthy else 0
                        if successes >= self._required_successes:
                            candidate: _WebsocketLogCandidate | None = None
                            try:
                                # Subscribe first. The candidate reader only
                                # fills its bounded queue until the handoff is
                                # committed below.
                                candidate = await self._open_candidate(
                                    address=address, topics=topics
                                )
                                if candidate is None:
                                    raise DataUnavailable(
                                        "BSC websocket candidate factory is unavailable"
                                    )
                                provider = getattr(self.http, "rpc_provider", self.http.provider)
                                barrier = await provider.get_latest_block_number()
                                if getattr(candidate, "error", None) is not None:
                                    raise candidate.error  # type: ignore[misc]
                                replayed = await provider.get_logs(
                                    from_block=self._http_replay_start(barrier),
                                    to_block=barrier,
                                    address=address,
                                    topics=topics,
                                )
                                if len(replayed) > self._handoff_buffer_max_events:
                                    raise HandoffBufferOverflow(
                                        "BSC HTTP handoff replay exceeds the configured event bound"
                                    )
                                for replay in sorted(replayed, key=_log_sort_key):
                                    if self._accept(replay):
                                        yield replay
                                drain = getattr(candidate, "drain_buffer", None)
                                if callable(drain):
                                    buffered = drain()
                                else:
                                    buffered = []
                                    while not candidate.queue.empty():
                                        item = candidate.queue.get_nowait()
                                        if item is _CANDIDATE_END:
                                            break
                                        buffered.append(item)
                                if getattr(candidate, "error", None) is not None:
                                    raise candidate.error  # type: ignore[misc]
                                for buffered_log in sorted(buffered, key=_log_sort_key):
                                    if self._accept(buffered_log):
                                        yield buffered_log
                                setter = getattr(self.websocket, "set_start_block", None)
                                if callable(setter):
                                    setter(barrier)
                                # Keep the candidate owned by this failover
                                # state machine.  The next outer iteration
                                # consumes it through the same committed-WS
                                # path used during startup handoff.
                                self.commit_prepared_candidate(candidate)
                                candidate_ready = candidate
                                self.active_provider = self.websocket.provider
                                self.active_transport = "ws"
                                self.failback_count += 1
                                metrics.increment("bsc_provider_failback_total")
                                source_health_registry.record_success(
                                    "bsc_websocket", details={"failback": True}
                                )
                                break
                            except Exception as exc:  # noqa: BLE001 - abort handoff
                                successes = 0
                                if candidate is not None:
                                    mark_failure = getattr(
                                        self.websocket, "mark_endpoint_failure", None
                                    )
                                    if callable(mark_failure):
                                        mark_failure(
                                            getattr(candidate, "error", None) or exc,
                                            endpoint_index=getattr(
                                                candidate, "endpoint_index", None
                                            ),
                                        )
                                    await candidate.close()
                                self.active_provider = self.http.provider
                                self.active_transport = "http"
                                record_failure = getattr(
                                    self.metrics, "record_provider_failure", None
                                )
                                if callable(record_failure):
                                    record_failure(exc)
                                source_health_registry.record_failure(
                                    "bsc_websocket", exc, details={"handoff": "aborted"}
                                )
                    if self._closed or candidate_ready is not None:
                        break
                    timeout = max(0.0, self._probe_seconds - (time.monotonic() - last_probe))
                    if pending_http is None:
                        pending_http = asyncio.create_task(http_iterator.__anext__())
                    done, _ = await asyncio.wait({pending_http}, timeout=timeout)
                    if not done:
                        continue
                    task = pending_http
                    pending_http = None
                    try:
                        log = task.result()
                    except StopAsyncIteration:
                        break
                    self.active_provider = self.http.provider
                    self.active_transport = "http"
                    self._sync_http_metrics()
                    if self._accept(log):
                        yield log
            finally:
                if pending_http is not None and not pending_http.done():
                    pending_http.cancel()
                    try:
                        await pending_http
                    except asyncio.CancelledError:
                        pass
                close_iterator = getattr(http_iterator, "aclose", None)
                if callable(close_iterator):
                    await close_iterator()
            if candidate_ready is not None:
                # ``candidate_ready`` is committed above; loop back through
                # the owner-managed initial-candidate branch.  This preserves
                # the failover owner across the first post-failback outage.
                continue


__all__ = [
    "BSCFailoverSubscription",
    "BSCHTTPPollingSubscription",
    "BSCWebsocketSubscription",
    "BlockWindowDedupe",
    "StreamMetrics",
]

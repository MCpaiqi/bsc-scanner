from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx

from smms.chains.base import Block, ChainLog, ChainTransaction, TransactionReceipt
from smms.config import Settings
from smms.ingestion import DataUnavailable


def _hex_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool):
        raise DataUnavailable(f"BSC RPC returned invalid {field}")
    try:
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            return int(value, 16) if value.startswith("0x") else int(value)
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
    ) -> None:
        if not settings.bsc_rpc_url:
            raise DataUnavailable("BSC_RPC_URL is not configured")
        self.url = settings.bsc_rpc_url
        self.chain_id = settings.bsc_chain_id
        self.client = client
        self._owned_client = client is None

    async def _request(self, method: str, params: list[Any]) -> Any:
        client = self.client or httpx.AsyncClient(timeout=10)
        try:
            response = await client.post(
                self.url,
                json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise DataUnavailable(f"BSC RPC {method} returned an invalid payload")
            if payload.get("error"):
                raise DataUnavailable(f"BSC RPC {method} failed: {payload['error']}")
            return payload.get("result")
        except DataUnavailable:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise DataUnavailable(f"BSC RPC {method} unavailable") from exc
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
    def _normalize_transaction(cls, item: dict[str, Any]) -> ChainTransaction:
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
        raw_transactions = result.get("transactions") or []
        transactions = tuple(
            self._normalize_transaction(item)
            for item in raw_transactions
            if isinstance(item, dict)
        )
        return Block(
            number=number,
            block_hash=str(block_hash),
            timestamp=_timestamp(result["timestamp"]),
            transactions=transactions,
            raw=dict(result),
        )

    async def get_transaction(self, transaction_hash: str) -> ChainTransaction | None:
        result = await self._request("eth_getTransactionByHash", [transaction_hash])
        if result is None:
            return None
        if not isinstance(result, dict):
            raise DataUnavailable("BSC transaction payload is invalid")
        return self._normalize_transaction(result)

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
        request: dict[str, Any] = {
            "fromBlock": self._block_tag(from_block),
            "toBlock": self._block_tag(to_block),
        }
        if address:
            request["address"] = address
        if topics:
            request["topics"] = list(topics)
        result = await self._request("eth_getLogs", [request])
        if result is None:
            return []
        if not isinstance(result, list):
            raise DataUnavailable("BSC logs payload is invalid")
        return [
            self._normalize_log(item)
            for item in result
            if isinstance(item, dict)
        ]


BscRPCProvider = BSCProvider
BscRpcProvider = BSCProvider
BSC_RPCProvider = BSCProvider

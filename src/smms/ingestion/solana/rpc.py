from typing import Any

import httpx

from smms.config.settings import Settings

from ..provider import DataProvider, DataUnavailable
from ..types import RawSwap, RawToken, RawTransaction


class SolanaRPCProvider(DataProvider):
    """Thin adapter over documented Solana JSON-RPC methods.

    Transaction interpretation is deliberately conservative: the raw RPC result is
    preserved in metadata and token amounts are only emitted when parsed transfer
    data is available.
    """

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        # Solana is retained only as historical code; active Settings no longer
        # exposes Solana endpoints in the BSC-only production runtime.
        raise DataUnavailable("Solana runtime is not supported; configure BSC RPC instead")
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
            if payload.get("error"):
                raise DataUnavailable(f"Solana RPC {method} failed: {payload['error']}")
            return payload.get("result")
        except (httpx.HTTPError, ValueError) as exc:
            raise DataUnavailable(f"Solana RPC {method} unavailable") from exc
        finally:
            if self._owned_client:
                await client.aclose()

    async def get_token(self, token_address: str) -> RawToken:
        result = await self._request("getAccountInfo", [token_address, {"encoding": "jsonParsed"}])
        value = result.get("value") if isinstance(result, dict) else None
        if not value:
            raise DataUnavailable(f"Token account not found: {token_address}")
        parsed = value.get("data", {}).get("parsed", {})
        info = parsed.get("info", {})
        return {
            "address": token_address,
            "chain": "solana",
            "decimals": int(info.get("decimals", 0)),
            "metadata": {"owner": value.get("owner"), "rpc": result},
        }

    async def get_transactions(
        self, address: str, *, before: str | None = None, limit: int = 100
    ) -> list[RawTransaction]:
        options: dict[str, Any] = {"limit": min(max(limit, 1), 1000)}
        if before:
            options["before"] = before
        signatures = await self._request("getSignaturesForAddress", [address, options])
        output: list[RawTransaction] = []
        for item in signatures or []:
            signature = item.get("signature")
            if not signature:
                continue
            transaction = await self._request(
                "getTransaction",
                [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
            )
            if not transaction:
                continue
            output.append(
                {
                    "signature": signature,
                    "slot": int(transaction.get("slot", item.get("slot", 0))),
                    "block_time": transaction.get("blockTime", item.get("blockTime")),
                    "wallet_address": address,
                    "side": "UNKNOWN",
                    "metadata": {"rpc": transaction, "signature_status": item},
                }
            )
        return output

    async def get_swaps(
        self, address: str, *, before: str | None = None, limit: int = 100
    ) -> list[RawSwap]:
        # Swap decoding is DEX/program-specific and belongs in provider enrichment.
        transactions = await self.get_transactions(address, before=before, limit=limit)
        return [
            {
                "signature": item["signature"],
                "wallet_address": address,
                "timestamp": item.get("block_time"),
                "metadata": {"raw_transaction": item},
            }
            for item in transactions
        ]

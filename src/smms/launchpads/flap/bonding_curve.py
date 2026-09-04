"""Injected, verification-first Flap bonding-curve access."""

import inspect
from collections.abc import Callable, Mapping
from decimal import Decimal
from typing import Any

from smms.ingestion import DataUnavailable
from smms.launchpads.base import BondingCurveState, BondingCurveStatus

from ._decoding import decimal_value

StateReader = Callable[[str, int | None], Any]
QuoteReader = Callable[[str, Decimal, str, int | None], Any]


class FlapBondingCurveAdapter:
    """Read curve state through a caller-supplied, chain-verified reader.

    Flap curve math is intentionally absent until the official contract and
    parameters are verified.  A quote therefore requires an injected quote
    reader rather than an invented formula.
    """

    def __init__(
        self,
        *,
        state_reader: StateReader | None = None,
        quote_reader: QuoteReader | None = None,
    ) -> None:
        self.state_reader = state_reader
        self.quote_reader = quote_reader

    async def get_price(self, token_address: str, *, block_number: int | None = None) -> Decimal | None:
        return (await self.get_state(token_address, block_number=block_number)).price

    async def get_quote(
        self,
        token_address: str,
        amount: Decimal,
        *,
        side: str,
        block_number: int | None = None,
    ) -> Decimal | None:
        if self.quote_reader is None:
            raise DataUnavailable(
                "Flap bonding-curve quote reader is not configured; VERIFY_ON_CHAIN"
            )
        if not isinstance(amount, Decimal):
            amount = Decimal(str(amount))
        if not amount.is_finite() or amount < 0:
            raise ValueError("amount must be a finite non-negative Decimal")
        normalized_side = str(side).upper()
        if normalized_side not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL")
        result = self.quote_reader(token_address, amount, normalized_side, block_number)
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, Mapping):
            result = result.get("quote")
        return decimal_value(result, field="bonding-curve quote")

    async def get_liquidity(
        self, token_address: str, *, block_number: int | None = None
    ) -> Decimal | None:
        return (await self.get_state(token_address, block_number=block_number)).liquidity

    async def get_progress(
        self, token_address: str, *, block_number: int | None = None
    ) -> Decimal | None:
        return (await self.get_state(token_address, block_number=block_number)).progress

    async def get_state(
        self, token_address: str, *, block_number: int | None = None
    ) -> BondingCurveState:
        if self.state_reader is None:
            raise DataUnavailable(
                "Flap bonding-curve state reader is not configured; VERIFY_ON_CHAIN"
            )
        if not token_address or not isinstance(token_address, str):
            raise ValueError("token_address is required")
        result = self.state_reader(token_address, block_number)
        if inspect.isawaitable(result):
            result = await result
        return self._state_from_result(result, block_number=block_number)

    @staticmethod
    def _state_from_result(
        result: Any,
        *,
        block_number: int | None,
    ) -> BondingCurveState:
        if isinstance(result, BondingCurveState):
            return result
        if not isinstance(result, Mapping):
            raise DataUnavailable("Flap bonding-curve state reader returned an invalid payload")

        raw_value = result.get("raw")
        raw = dict(raw_value) if isinstance(raw_value, Mapping) else {}
        if result.get("verified") is False or raw.get("verified") is False:
            raise DataUnavailable("Flap bonding-curve state is not verified")

        status_value = result.get("status", BondingCurveStatus.UNKNOWN.value)
        if isinstance(status_value, BondingCurveStatus):
            status = status_value
        else:
            try:
                status = BondingCurveStatus(str(status_value).upper())
            except ValueError:
                status = BondingCurveStatus.UNKNOWN

        as_of_block = result.get("as_of_block", block_number)
        if as_of_block is not None:
            if isinstance(as_of_block, bool):
                raise DataUnavailable("Flap bonding-curve state block is invalid")
            try:
                as_of_block = int(as_of_block)
            except (TypeError, ValueError) as exc:
                raise DataUnavailable("Flap bonding-curve state block is invalid") from exc
            if as_of_block < 0:
                raise DataUnavailable("Flap bonding-curve state block is invalid")

        return BondingCurveState(
            status=status,
            price=decimal_value(result.get("price"), field="bonding-curve price"),
            liquidity=decimal_value(result.get("liquidity"), field="bonding-curve liquidity"),
            progress=decimal_value(result.get("progress"), field="bonding-curve progress"),
            reserve_native=decimal_value(
                result.get("reserve_native"), field="bonding-curve native reserve"
            ),
            reserve_token=decimal_value(
                result.get("reserve_token"), field="bonding-curve token reserve"
            ),
            as_of_block=as_of_block,
            raw=raw,
        )


__all__ = ["FlapBondingCurveAdapter", "QuoteReader", "StateReader"]

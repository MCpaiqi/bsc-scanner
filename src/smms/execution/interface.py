from typing import Protocol


class ExecutionInterface(Protocol):
    async def buy(self, token: str, quantity: float, price: float) -> dict[str, object]: ...

    async def sell(self, token: str, quantity: float, price: float) -> dict[str, object]: ...


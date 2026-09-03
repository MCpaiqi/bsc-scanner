class PaperExecution:
    """Deterministic fill adapter for the future paper-trading engine."""

    async def buy(self, token: str, quantity: float, price: float) -> dict[str, object]:
        return {"mode": "PAPER", "side": "BUY", "token": token, "quantity": quantity, "price": price}

    async def sell(self, token: str, quantity: float, price: float) -> dict[str, object]:
        return {"mode": "PAPER", "side": "SELL", "token": token, "quantity": quantity, "price": price}


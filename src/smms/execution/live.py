class LiveTradingDisabled(RuntimeError):
    pass


class LiveExecution:
    """Placeholder: live execution is intentionally unavailable in Phase 0."""

    async def buy(self, *args, **kwargs):
        raise LiveTradingDisabled("Live trading is disabled in Phase 0")

    async def sell(self, *args, **kwargs):
        raise LiveTradingDisabled("Live trading is disabled in Phase 0")


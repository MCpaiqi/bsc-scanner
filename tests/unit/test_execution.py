import pytest

from smms.execution import LiveExecution, LiveTradingDisabled


@pytest.mark.asyncio
async def test_live_execution_is_guarded() -> None:
    with pytest.raises(LiveTradingDisabled):
        await LiveExecution().buy("token", 1)


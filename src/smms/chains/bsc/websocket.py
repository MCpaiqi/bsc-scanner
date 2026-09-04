from collections.abc import AsyncIterator, Sequence

from smms.chains.base import ChainLog
from smms.config import Settings
from smms.ingestion import DataUnavailable


class BSCWebsocketSubscription:
    """Explicit placeholder until a reconnecting subscription implementation is validated."""

    def __init__(self, settings: Settings) -> None:
        if not settings.bsc_ws_url:
            raise DataUnavailable("BSC_WS_URL is not configured")
        self.url = settings.bsc_ws_url

    def subscribe_logs(
        self, *, address: str | None = None, topics: Sequence[str] | None = None
    ) -> AsyncIterator[ChainLog]:
        del address, topics
        raise DataUnavailable("BSC WebSocket subscriptions are not implemented")


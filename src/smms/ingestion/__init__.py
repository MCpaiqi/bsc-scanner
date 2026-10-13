from .mock import MockProvider
from .provider import DataProvider, DataUnavailable


def __getattr__(name: str):
    """Load persistence-backed ingestion implementations only when requested."""

    if name == "BSCRealtimeIngestionService":
        from .bsc_stream import BSCRealtimeIngestionService

        return BSCRealtimeIngestionService
    if name == "IngestionService":
        from .service import IngestionService

        return IngestionService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = ["BSCRealtimeIngestionService", "DataProvider", "DataUnavailable", "IngestionService", "MockProvider"]

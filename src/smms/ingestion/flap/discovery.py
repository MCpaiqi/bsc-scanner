"""Application-level Flap token discovery entry point."""

from smms.chains.base import ChainProvider
from smms.launchpads.base import LaunchpadTokenDiscovery
from smms.launchpads.flap import FlapConfig

from .historical import FlapHistoricalIngestionService


async def discover_flap_tokens(
    provider: ChainProvider,
    config: FlapConfig,
    *,
    from_block: int,
    to_block: int,
) -> list[LaunchpadTokenDiscovery]:
    """Discover Flap creations through the application service."""

    return await FlapHistoricalIngestionService(provider, config).discover_tokens(
        from_block=from_block,
        to_block=to_block,
    )


__all__ = ["FlapHistoricalIngestionService", "discover_flap_tokens"]

"""GoPlus external evidence provider."""

import asyncio
import hashlib
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from smms.chains.constants import BSC_CHAIN_ID
from smms.evidence.providers import GoPlusEvidenceProvider as _Provider

from .models import EvidenceLevel, EvidenceObservation, SubjectType
from .provider import Capability, ExternalEvidenceProviderBase


class GoPlusAccessTokenManager:
    """Obtain and cache GoPlus bearer tokens using the documented SHA1 flow."""

    def __init__(
        self,
        *,
        app_key: str,
        app_secret: str,
        client: Any = None,
        base_url: str,
        timeout: float = 10,
    ) -> None:
        if not app_key or not app_secret:
            raise ValueError("GoPlus app_key and app_secret are required")
        self.app_key, self.app_secret, self.client = app_key, app_secret, client
        self.base_url, self.timeout = base_url.rstrip("/"), timeout
        self._token: str | None = None
        self._expires_at = datetime.min.replace(tzinfo=UTC)
        self._lock = asyncio.Lock()

    async def get_token(self) -> str:
        now = datetime.now(UTC)
        if self._token and now < self._expires_at - timedelta(seconds=30):
            return self._token
        async with self._lock:
            now = datetime.now(UTC)
            if self._token and now < self._expires_at - timedelta(seconds=30):
                return self._token
            timestamp = int(time.time())
            signature = hashlib.sha1(
                f"{self.app_key}{timestamp}{self.app_secret}".encode()
            ).hexdigest()
            owns_client = self.client is None
            client = self.client
            if owns_client:
                import httpx

                client = httpx.AsyncClient(timeout=self.timeout)
            try:
                response = await client.post(
                    f"{self.base_url}/api/v1/token",
                    json={"app_key": self.app_key, "sign": signature, "time": timestamp},
                    timeout=self.timeout,
                )
                response.raise_for_status()
                payload = response.json()
            finally:
                if owns_client:
                    await client.aclose()
            result = payload.get("result") if isinstance(payload, dict) else None
            token = result.get("access_token") if isinstance(result, dict) else None
            expires_in = result.get("expires_in") if isinstance(result, dict) else None
            if not token:
                raise RuntimeError("GoPlus token response did not contain access_token")
            self._token = str(token)
            self._expires_at = now + timedelta(seconds=max(1, int(expires_in or 300)))
            return self._token


class GoPlusProvider(ExternalEvidenceProviderBase):
    provider_name = "goplus"
    capabilities = frozenset({Capability.TOKEN_SECURITY, Capability.HOLDER_INFORMATION})

    def __init__(
        self,
        *,
        chain_id: str = str(BSC_CHAIN_ID),
        client: Any = None,
        base_url: str = "https://api.gopluslabs.io",
        api_key: str | None = None,
        app_key: str | None = None,
        app_secret: str | None = None,
        timeout: float = 10,
        ttl: float = 60,
        stale_ttl: float = 300,
        rate_limit: float = 0,
        enabled: bool = True,
        **kwargs: Any,
    ) -> None:
        self.chain = kwargs.pop("chain", "bsc")
        manager = (
            GoPlusAccessTokenManager(
                app_key=app_key,
                app_secret=app_secret,
                client=client,
                base_url=base_url,
                timeout=timeout,
            )
            if app_key and app_secret
            else None
        )
        self._provider = _Provider(
            chain_id=chain_id,
            client=client,
            base_url=base_url,
            api_key=api_key,
            app_key=app_key,
            app_secret=app_secret,
            access_token_manager=manager,
            timeout=timeout,
            ttl=ttl,
            stale_ttl=stale_ttl,
            rate_limit=rate_limit,
            enabled=enabled,
            **kwargs,
        )
        self.health = self._provider.health
        self._cache = self._provider._cache

    async def _convert(
        self, chain: str, address: str, capability: str
    ) -> list[EvidenceObservation]:
        self._provider.chain = chain
        values = await getattr(self._provider, f"fetch_{capability}")(address)
        now = datetime.now(UTC)
        return [
            EvidenceObservation(
                chain=chain,
                subject_type=SubjectType.TOKEN,
                subject_address=address,
                field=item.field,
                value=item.value,
                source=self.provider_name,
                source_type="secondary_api",
                observed_at=item.observed_at or now,
                fetched_at=item.fetched_at or now,
                confidence=item.confidence,
                evidence_level=EvidenceLevel.UNKNOWN
                if item.value is None
                else EvidenceLevel.TRUSTED_PROVIDER,
                raw_reference=item.raw_reference,
                expires_at=item.expires_at,
                stale=item.stale,
            )
            for item in values
        ]

    async def fetch_token_security(self, chain_or_address: str, token_address: str | None = None):
        chain, address = (
            (self.chain, chain_or_address)
            if token_address is None
            else (chain_or_address, token_address)
        )
        return await self._convert(chain, address, "token_security")

    async def fetch_holder_information(
        self, chain_or_address: str, token_address: str | None = None
    ):
        chain, address = (
            (self.chain, chain_or_address)
            if token_address is None
            else (chain_or_address, token_address)
        )
        return await self._convert(chain, address, "holder_information")


GoPlusEvidenceProvider = GoPlusProvider
__all__ = ["GoPlusAccessTokenManager", "GoPlusEvidenceProvider", "GoPlusProvider"]

"""Discovered token names recover after incomplete or failed ERC-20 reads."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from smms.db.models import Base, Token
from smms.market.metadata import _selector, backfill_missing_erc20_metadata, enrich_erc20_metadata


def _string(value: str) -> str:
    raw = value.encode()
    return "0x" + f"{32:064x}{len(raw):064x}" + raw.hex().ljust(64, "0")


class MetadataProvider:
    def __init__(self, broken_address: str | None = None):
        self.broken_address = broken_address

    async def eth_call(self, address: str, selector: str) -> str:
        if address == self.broken_address:
            raise TimeoutError("temporary provider failure")
        if selector == _selector("symbol()"):
            return _string("RECOVERED")
        if selector == _selector("name()"):
            return _string("Recovered Token")
        return "0x" + f"{18:064x}"


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_is_newest", [False, True])
async def test_failed_token_does_not_starve_other_token_name_backfill(failed_is_newest):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            old = Token(chain="bsc", address="0x" + "1" * 40)
            new = Token(
                chain="bsc", address="0x" + "2" * 40,
                metadata_json={"metadata_backfill_last_attempt_at": "2000-01-01T00:00:00+00:00"},
            )
            session.add_all([old, new])
            await session.flush()
            failed, healthy = (new, old) if failed_is_newest else (old, new)
            provider = MetadataProvider(broken_address=failed.address)
            for _ in range(2):
                await backfill_missing_erc20_metadata(
                    session, provider, batch_size=1, rate_limit_per_second=0, retries=0
                )
            assert healthy.name == "Recovered Token"
            assert healthy.symbol == "RECOVERED"
            assert failed.metadata_json["metadata_backfill_attempts"] == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_value", ["", " \t\r\n", "Unknown token", "—", "?"])
async def test_blank_metadata_fields_are_backfilled(missing_value):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            token = Token(
                chain="bsc", address="0x" + "3" * 40,
                symbol=missing_value, name=missing_value, decimals=18,
            )
            session.add(token)
            await session.flush()
            await backfill_missing_erc20_metadata(
                session, MetadataProvider(), rate_limit_per_second=0, retries=0
            )
            assert token.name == "Recovered Token"
            assert token.symbol == "RECOVERED"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_latest_discovery_is_not_delayed_by_unattempted_historical_backlog():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            old = Token(chain="bsc", address="0x" + "6" * 40)
            current = Token(chain="bsc", address="0x" + "7" * 40)
            session.add_all([old, current])
            await session.flush()
            await backfill_missing_erc20_metadata(
                session, MetadataProvider(), batch_size=1, rate_limit_per_second=0, retries=0
            )
            assert current.name == "Recovered Token"
            assert old.name is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_partial_response_keeps_existing_name_and_completes_metadata_status():
    class PartialProvider:
        async def eth_call(self, _address, selector):
            return _string("FILLED") if selector == _selector("symbol()") else "0x"

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            token = Token(
                chain="bsc", address="0x" + "8" * 40, name="Existing Token", decimals=0,
            )
            session.add(token)
            await session.flush()
            stats = await backfill_missing_erc20_metadata(
                session, PartialProvider(), rate_limit_per_second=0, retries=0
            )
            assert token.name == "Existing Token"
            assert token.symbol == "FILLED"
            assert token.decimals == 0
            assert token.metadata_json["metadata_status"] == "KNOWN"
            assert token.metadata_json["metadata_backfill_reason_code"] is None
            assert stats == {"attempted": 1, "updated": 1, "failed": 0}
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_empty_abi_result_is_not_successful_metadata():
    class EmptyProvider:
        async def eth_call(self, *_args):
            return "0x"

    metadata = await enrich_erc20_metadata(EmptyProvider(), "0x" + "4" * 40, retries=0)
    assert metadata["name"] is None
    assert metadata["metadata_status"] == "UNKNOWN"
    assert metadata["metadata_reason_code"] is not None


@pytest.mark.asyncio
async def test_decimals_alone_do_not_mark_missing_name_as_known():
    class DecimalsProvider:
        async def eth_call(self, _address, selector):
            return "0x" + f"{18:064x}" if selector == _selector("decimals()") else "0x"

    metadata = await enrich_erc20_metadata(DecimalsProvider(), "0x" + "4" * 40, retries=0)
    assert metadata["name"] is None
    assert metadata["metadata_status"] == "PARTIAL"
    assert metadata["metadata_reason_code"] == "METADATA_PARTIAL"


@pytest.mark.asyncio
async def test_running_bsc_worker_recovers_names_with_dexscreener_disabled(monkeypatch):
    from smms import runtime
    from smms.chains import bsc

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    task = None
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            session.add(Token(chain="bsc", address="0x" + "5" * 40))
            await session.commit()

        recovered = asyncio.Event()

        @asynccontextmanager
        async def scope(_settings):
            async with factory() as session:
                yield session
                token = await session.scalar(select(Token))
                if token.name == "Recovered Token":
                    recovered.set()

        async def heartbeat(*_args, **_kwargs):
            return None

        settings = SimpleNamespace(
            bsc_rpc_endpoints=("https://rpc.example",), bsc_ws_endpoints=(),
            bsc_dex_factory_addresses="", bsc_chain_id=56,
            dexscreener_enabled=False, runtime_heartbeat_interval_seconds=10,
            market_snapshot_interval_seconds=30,
        )
        monkeypatch.setattr(runtime, "get_settings", lambda: settings)
        monkeypatch.setattr(runtime, "session_scope", scope)
        monkeypatch.setattr(runtime, "heartbeat", heartbeat)
        monkeypatch.setattr(bsc, "BSCProvider", lambda *_args, **_kwargs: MetadataProvider())
        task = asyncio.create_task(runtime.run_bsc_role(
            worker_name="bsc-backfill", stream_name="test", start_from_head=False,
            enable_market_enrichment=True,
        ))
        await asyncio.wait_for(recovered.wait(), timeout=0.5)
        async with factory() as session:
            assert (await session.scalar(select(Token))).name == "Recovered Token"
    finally:
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        await engine.dispose()

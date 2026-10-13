import asyncio
import inspect
import json
from datetime import UTC, datetime

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from smms.api.analyst import RulePayload, _validated_rule_payload
from smms.api.app import _validated_chain, risk_tokens
from smms.chains.base import ChainLog
from smms.chains.bsc.rpc import BSCProvider
from smms.chains.bsc.websocket import BSCFailoverSubscription, BSCWebsocketSubscription
from smms.chains.constants import BSC_CHAIN_ID
from smms.config import Settings
from smms.db.models import Base, RawChainEvent
from smms.db.repositories import RawChainEventRepository, StreamCheckpointRepository
from smms.ingestion import DataUnavailable
from smms.smart_money.intelligence import build_wallet_knowledge_graph


def _log(tx: str, block: int, index: int = 0, block_hash: str | None = None, removed: bool = False) -> ChainLog:
    return ChainLog(
        address="0x" + "1" * 40,
        topics=("0xtopic",),
        data="0x",
        block_number=block,
        transaction_hash=tx,
        log_index=index,
        transaction_index=0,
        block_hash=block_hash or f"0xblock{block}",
        removed=removed,
    )


def test_bsc_only_api_chain_boundary() -> None:
    assert _validated_chain(None) == "bsc"
    assert _validated_chain("bsc") == "bsc"
    for value in ("solana", "ethereum", "eth", "polygon"):
        with pytest.raises(HTTPException) as exc_info:
            _validated_chain(value)
        assert exc_info.value.status_code == 400
        assert "当前系统仅支持 BNB Smart Chain（BSC）" in str(exc_info.value.detail)


def test_requested_bsc_ws_setting_aliases_are_supported() -> None:
    settings = Settings(
        _env_file=None,
        BSC_WS_TIMEOUT_SECONDS=7,
        BSC_WS_MAX_RETRIES=4,
    )
    assert settings.bsc_ws_connect_timeout_seconds == 7
    assert settings.bsc_ws_reconnect_max_retries == 4


@pytest.mark.asyncio
async def test_risk_listing_rejects_non_bsc_chain_before_database_access() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await risk_tokens(chain="solana")
    assert exc_info.value.status_code == 400


def test_alert_rule_api_defaults_to_bsc_and_rejects_other_chains() -> None:
    payload = RulePayload(
        name="BSC liquidity",
        conditions=[{"field": "liquidity", "operator": ">=", "value": 1}],
    )
    assert _validated_rule_payload(payload)["chain"] == "bsc"
    with pytest.raises(HTTPException) as exc_info:
        _validated_rule_payload(payload.model_copy(update={"chain": "solana"}))
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_wrong_chain_http_endpoint_is_rejected_before_use() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        method = json.loads(request.content)["method"]
        result = {"eth_chainId": "0x1", "eth_blockNumber": "0x64", "eth_getLogs": []}.get(method)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": result})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = BSCProvider(
        Settings(_env_file=None, BSC_RPC_URL="https://wrong.example", BSC_RPC_MAX_RETRIES=0),
        client=client,
        verify_on_use=True,
    )
    with pytest.raises(DataUnavailable):
        await provider.ensure_usable()
    assert provider.status()["usable"] is False
    assert provider.endpoint_status()[0]["chain_verified"] is False
    await client.aclose()


class _WrongChainSocket:
    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []

    async def send(self, value: str) -> None:
        self.sent.append(json.loads(value))

    async def recv(self) -> str:
        return json.dumps({"jsonrpc": "2.0", "id": 0, "result": "0x1"})

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration

    async def close(self) -> None:
        return None


class _DisconnectingSocket:
    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []

    async def send(self, value: str) -> None:
        self.sent.append(json.loads(value))

    async def recv(self) -> str:
        request = self.sent[-1]
        result = "0x38" if request["method"] == "eth_chainId" else "sub-1"
        return json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result})

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise ConnectionError("ws dropped")

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_wrong_chain_websocket_is_rejected() -> None:
    async def connect(*args, **kwargs):
        return _WrongChainSocket()

    settings = Settings(
        _env_file=None,
        BSC_WS_URL="wss://wrong.example",
        BSC_WS_MAX_RETRIES=0,
        BSC_WS_BACKOFF_SECONDS=0,
        BSC_WS_JITTER_SECONDS=0,
    )
    stream = BSCWebsocketSubscription(settings, connect=connect)
    with pytest.raises(DataUnavailable):
        await anext(stream.subscribe_logs())
    assert stream.status()["usable"] is False
    assert stream.endpoint_status()[0]["chain_verified"] is False


@pytest.mark.asyncio
async def test_failover_managed_websocket_surfaces_disconnect_to_http() -> None:
    calls = 0

    async def connect(*args, **kwargs):
        nonlocal calls
        calls += 1
        return _DisconnectingSocket()

    settings = Settings(
        _env_file=None,
        BSC_WS_URL="wss://bsc.example",
        BSC_WS_MAX_RETRIES=5,
        BSC_WS_BACKOFF_SECONDS=0,
        BSC_WS_JITTER_SECONDS=0,
    )
    stream = BSCWebsocketSubscription(settings, connect=connect)
    stream.failover_managed = True
    with pytest.raises(DataUnavailable):
        await anext(stream.subscribe_logs())
    assert calls == 1


def test_reorg_replacement_identity_is_effectively_once_but_canonical_aware() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        first = RawChainEvent(
            chain_id=BSC_CHAIN_ID,
            block_number=100,
            block_hash="0xold",
            transaction_hash="0xtx",
            log_index=1,
            contract_address="0xcontract",
            topics=[],
            data="0x",
            ingestion_time=datetime.now(UTC),
            provider="bsc",
            confirmation_status="confirmed",
            removed=False,
            raw_payload_json={},
        )
        old = RawChainEventRepository(session).add_idempotent(first)
        duplicate = RawChainEventRepository(session).add_idempotent(
            RawChainEvent(
                chain_id=BSC_CHAIN_ID,
                block_number=100,
                block_hash="0xOLD",
                transaction_hash="0xTX",
                log_index=1,
                contract_address="0xcontract",
                topics=[],
                data="0x",
                ingestion_time=datetime.now(UTC),
                provider="bsc",
                confirmation_status="confirmed",
                removed=False,
                raw_payload_json={},
            )
        )
        replacement = RawChainEvent(
            chain_id=BSC_CHAIN_ID,
            block_number=100,
            block_hash="0xnew",
            transaction_hash="0xtx",
            log_index=1,
            contract_address="0xcontract",
            topics=[],
            data="0xreplacement",
            ingestion_time=datetime.now(UTC),
            provider="bsc",
            confirmation_status="confirmed",
            removed=False,
            raw_payload_json={},
        )
        new = RawChainEventRepository(session).add_idempotent(replacement)
        session.commit()
        assert old.id == duplicate.id
        assert new.id != old.id
        assert session.query(RawChainEvent).count() == 2


def test_raw_event_identity_rejects_duplicate_null_block_hash() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    values = {
        "chain_id": BSC_CHAIN_ID,
        "block_number": 100,
        "block_hash": None,
        "transaction_hash": "0xtx",
        "log_index": 1,
        "contract_address": "0xcontract",
        "topics": [],
        "data": "0x",
        "ingestion_time": datetime.now(UTC),
        "provider": "bsc",
        "confirmation_status": "confirmed",
        "removed": False,
        "raw_payload_json": {},
    }
    with Session(engine) as session:
        session.add(RawChainEvent(**values))
        session.commit()
        session.add(RawChainEvent(**values))
        with pytest.raises(IntegrityError):
            session.commit()


class _FakeCandidate:
    def __init__(self, values: list[ChainLog], error: Exception | None = None) -> None:
        self.queue: asyncio.Queue[object] = asyncio.Queue()
        for value in values:
            self.queue.put_nowait(value)
        self.error = error
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self) -> ChainLog:
        if self.queue.empty():
            if self.error:
                raise self.error
            raise StopAsyncIteration
        return await self.queue.get()

    async def close(self) -> None:
        self.closed = True


class _FakeWebsocketStream:
    provider = "bsc_websocket"

    def __init__(self, candidate: _FakeCandidate | None) -> None:
        self.settings = Settings(_env_file=None, BSC_WS_URL="wss://bsc.example", BSC_WS_FAILBACK_HEALTHY_PROBES=1)
        self.metrics = type("M", (), {"current_block": None, "event_count": 0, "reconnect_count": 0, "provider_errors": 0, "decode_failures": 0, "ingestion_latency_ms": 0, "last_event_at": None})()
        self.candidate = candidate
        self.calls = 0

    async def subscribe_logs(self, **kwargs):
        if self.calls == 0:
            self.calls += 1
            raise ConnectionError("ws dropped")
        while True:
            await asyncio.sleep(3600)
            yield _log("never", 999)

    async def probe_health(self) -> bool:
        return True

    async def open_log_candidate(self, **kwargs):
        return self.candidate

    def set_start_block(self, value):
        self.start_block = value

    async def close(self):
        return None


class _FakeHTTPProvider:
    provider = "bsc_http_polling"

    def __init__(self, replay: list[ChainLog]) -> None:
        self.replay = replay

    async def get_latest_block_number(self) -> int:
        return 102

    async def get_logs(self, **kwargs) -> list[ChainLog]:
        return list(self.replay)


class _FakeHTTPSubscription:
    provider = "bsc_http_polling"

    def __init__(self, provider: _FakeHTTPProvider, values: list[ChainLog]) -> None:
        self.rpc_provider = provider
        self.values = values
        self.metrics = type("M", (), {"current_block": 102, "event_count": 0, "reconnect_count": 0, "provider_errors": 0, "decode_failures": 0, "ingestion_latency_ms": 0, "last_event_at": None})()

    def set_start_block(self, value):
        self.start_block = value

    async def subscribe_logs(self, **kwargs):
        for value in self.values:
            yield value
        await asyncio.sleep(3600)

    async def close(self):
        return None


@pytest.mark.asyncio
async def test_zero_gap_failback_buffers_candidate_and_deduplicates_boundary() -> None:
    a, b, c, d = _log("a", 100), _log("b", 101), _log("c", 102), _log("d", 102)
    candidate = _FakeCandidate([a, d])
    websocket = _FakeWebsocketStream(candidate)
    http_provider = _FakeHTTPProvider([a, b, c])
    http = _FakeHTTPSubscription(http_provider, [a, b, c])
    failover = BSCFailoverSubscription(websocket, http, probe_seconds=0, required_successes=1, sleep=lambda _: asyncio.sleep(0))
    failover.observe_checkpoint(101, 0, "b")
    output: list[ChainLog] = []
    async def consume() -> None:
        async for value in failover.subscribe_logs():
            output.append(value)
            if {item.transaction_hash for item in output} >= {"a", "b", "c", "d"}:
                await failover.close()
    await asyncio.wait_for(consume(), timeout=2)
    assert [item.transaction_hash for item in output].count("a") == 1
    assert {item.transaction_hash for item in output} == {"a", "b", "c", "d"}


@pytest.mark.asyncio
async def test_handoff_candidate_failure_keeps_http_primary() -> None:
    replacement = _log("http-only", 102)
    candidate = _FakeCandidate([], error=ConnectionError("candidate dropped"))
    websocket = _FakeWebsocketStream(candidate)
    http_provider = _FakeHTTPProvider([replacement])
    http = _FakeHTTPSubscription(http_provider, [replacement])
    failover = BSCFailoverSubscription(
        websocket, http, probe_seconds=0, required_successes=1, sleep=lambda _: asyncio.sleep(0)
    )
    output: list[ChainLog] = []

    async for value in failover.subscribe_logs():
        output.append(value)
        await failover.close()
        break

    assert [item.transaction_hash for item in output] == ["http-only"]
    assert failover.active_transport == "http"
    assert failover.failback_count == 0


def test_durable_checkpoint_survives_repository_recreation() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        repository = StreamCheckpointRepository(session)
        repository.upsert(
            chain_id=BSC_CHAIN_ID,
            provider="bsc",
            stream_name="bsc_realtime",
            status="running",
            block_number=101,
            log_index=7,
            transaction_hash="0xcheckpoint",
        )
        session.commit()

    with Session(engine) as restarted_session:
        restored = StreamCheckpointRepository(restarted_session).get(
            BSC_CHAIN_ID, "bsc", "bsc_realtime"
        )
        assert restored is not None
        assert restored.last_processed_block == 101
        assert restored.last_processed_log == 7
        assert restored.last_processed_transaction_hash == "0xcheckpoint"


def test_graph_total_and_edge_bounds_are_reported() -> None:
    now = datetime.now(UTC)
    trades = [
        {"wallet_address": f"wallet-{i}", "token_address": f"token-{i}", "side": "BUY", "timestamp": now, "usd_value": 1}
        for i in range(20)
    ]
    graph = build_wallet_knowledge_graph(trades, max_total_nodes=5, max_graph_edges=1, max_source_events=10, max_pair_checks=2)
    assert len(graph.wallets) + len(graph.tokens) + len(graph.trades) <= 5
    assert len(graph.relationships) <= 1
    assert graph.truncated is True
    assert graph.truncation_reasons


def test_wallet_ranking_keeps_order_and_limit_in_sql() -> None:
    from smms.api.app import smart_money_wallets

    source = inspect.getsource(smart_money_wallets)
    assert ".order_by(" in source
    assert ".limit(limit)" in source


def test_provider_status_never_contains_configured_secret_url() -> None:
    settings = Settings(_env_file=None, BSC_RPC_URL="https://user:secret@rpc.example/key", BSC_WS_URL="wss://user:secret@ws.example/key")
    rpc = BSCProvider(settings)
    ws = BSCWebsocketSubscription(settings, connect=lambda *args, **kwargs: None)
    assert "secret" not in json.dumps(rpc.status())
    assert "rpc.example" not in json.dumps(rpc.status())
    assert "secret" not in json.dumps(ws.status())
    assert "ws.example" not in json.dumps(ws.status())


def test_failed_ws_endpoint_is_not_reported_healthy() -> None:
    settings = Settings(_env_file=None, BSC_WS_URL="wss://bsc.example")
    websocket = BSCWebsocketSubscription(settings, connect=lambda *args, **kwargs: None)
    websocket.mark_endpoint_failure(ConnectionError("candidate dropped"), endpoint_index=0)
    endpoint = websocket.status()["endpoints"][0]
    assert endpoint["usable"] is False
    assert endpoint["chain_verified"] is False
    assert endpoint["degraded"] is True

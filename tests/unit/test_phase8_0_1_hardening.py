import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from smms.api.app import app
from smms.chains.constants import normalize_chain
from smms.external_evidence import GoPlusEvidenceProvider
from smms.smart_money import build_wallet_knowledge_graph

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _trade(wallet: str, token: str, minutes: int) -> dict[str, object]:
    return {
        "wallet_address": wallet,
        "token_address": token,
        "side": "BUY",
        "timestamp": T0 + timedelta(minutes=minutes),
        "usd_value": 1,
    }


def test_chain_normalization_defaults_to_bsc_and_rejects_unknown_values() -> None:
    assert normalize_chain(None) == "bsc"
    assert normalize_chain(" SOLANA ") == "solana"
    with pytest.raises(ValueError):
        normalize_chain("ethereum")


def test_graph_reports_token_node_truncation_and_preserves_as_of() -> None:
    trades = [_trade("wallet", f"token-{index}", index) for index in range(5)]
    graph = build_wallet_knowledge_graph(
        trades,
        as_of=T0 + timedelta(minutes=3),
        max_graph_nodes=2,
    )
    assert len(graph.tokens) == 2
    assert graph.truncated is True
    assert graph.truncation["tokens_examined"] == 4
    assert graph.truncation["tokens_included"] == 2
    assert graph.truncation["tokens_dropped"] == 2
    assert all(event.timestamp <= T0 + timedelta(minutes=3) for event in graph.trades)


def test_graph_aggregates_repeated_co_buy_evidence() -> None:
    graph = build_wallet_knowledge_graph(
        [
            _trade("a", "x", 0),
            _trade("b", "x", 1),
            _trade("a", "y", 2),
            _trade("b", "y", 3),
        ],
        as_of=T0 + timedelta(minutes=5),
    )
    repeated = next(edge for edge in graph.relationships if edge.kind == "repeated_co_buy")
    assert repeated.evidence["evidence_count"] == 1
    assert repeated.evidence["token_count"] == 2
    assert repeated.evidence["tokens"] == ["x", "y"]


def test_radar_rejects_invalid_chain_before_database_access() -> None:
    from fastapi.testclient import TestClient

    response = TestClient(app).get("/api/radar/candidates?chain=ethereum")
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_goplus_uses_bearer_access_token_and_never_query_access_token() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"result": {"access_token": "bearer-token", "expires_in": 600}})
        return httpx.Response(200, json={"result": {"MINT": {"is_honeypot": "0"}}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://fixture.test") as client:
        provider = GoPlusEvidenceProvider(
            client=client,
            chain_id="56",
            base_url="https://fixture.test",
            app_key="app",
            app_secret="secret",
            ttl=0,
        )
        await provider.fetch_token_security("MINT")

    security = next(request for request in requests if "token_security" in request.url.path)
    assert security.headers["Authorization"] == "Bearer bearer-token"
    assert "access-token" not in str(security.url)
    token_request = next(request for request in requests if request.url.path.endswith("/token"))
    payload = json.loads(token_request.content)
    assert payload["app_key"] == "app"
    assert "secret" not in str(token_request.url)

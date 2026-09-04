from fastapi.testclient import TestClient

from smms.api.app import app


def test_health_endpoint_reports_paper_mode() -> None:
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["trading_mode"] == "PAPER"
    assert payload["live_trading_enabled"] is False
    assert payload["bsc_rpc"] == "not_configured"

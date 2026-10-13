import importlib.util
import subprocess
import sys
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from test_phase7_7_2_dataset import T0, _session

from smms.backtest.dataset import HistoricalDatasetBuilder
from smms.backtest.signal_engine import FeaturePriceHistory, generate_signal, run_backtest
from smms.db.models import Pair, Token, TokenLifecycleEvent, TradeEvent


def test_launchpad_before_pair_and_no_future_lifecycle_leak():
    with _session() as session:
        session.add(Token(address="flap", chain="bsc", launchpad="flap", first_seen_at=T0))
        session.commit()
        builder = HistoricalDatasetBuilder(session)
        before, _ = builder.build(T0, T0)
        session.add(Pair(chain_id=56, address="pair", token0_address="flap",
                         token1_address="bnb", creation_timestamp=T0 + timedelta(minutes=16)))
        session.add(TokenLifecycleEvent(chain="bsc", token_address="flap", launchpad="flap",
                         event_type="MIGRATED", timestamp=T0 + timedelta(minutes=15), tx_hash="m"))
        session.add(TradeEvent(chain="bsc", token_address="flap", launchpad="flap",
                         trader_address="w", tx_hash="t", side="BUY", price=2,
                         timestamp=T0 + timedelta(minutes=1), source="fixture"))
        session.commit()
        after, _ = builder.build(T0, T0)
        assert before[0].market == after[0].market
        assert before[0].feature_price_history == after[0].feature_price_history
        assert before[0].market_lifecycle == after[0].market_lifecycle == "LAUNCHPAD_DISCOVERED"
        rows, _ = builder.build(T0, T0 + timedelta(minutes=16))
        assert [r.market_lifecycle for r in rows] == [
            "LAUNCHPAD_DISCOVERED", "LAUNCHPAD_ACTIVE", "MIGRATED_TO_DEX", "MIGRATED_TO_DEX"]


def test_dex_eligibility_starts_at_pair_even_without_trade():
    with _session() as session:
        session.add(Token(address="dex", chain="bsc", creation_timestamp=T0))
        pair = Pair(chain_id=56, address="pair", token0_address="dex", token1_address="bnb")
        session.add(pair)
        session.commit()
        builder = HistoricalDatasetBuilder(session)
        assert builder.build(T0, T0)[0] == []  # unknown timestamp is not evidence
        pair.creation_timestamp = T0 + timedelta(minutes=16)
        session.commit()
        assert builder.build(T0, T0)[0] == []
        rows, _ = builder.build(T0, T0 + timedelta(minutes=20))
        assert rows[0].signal_timestamp == T0 + timedelta(minutes=16)
        assert rows[0].discovery_timestamp == rows[0].signal_timestamp
        assert rows[0].market_lifecycle == "DEX_PAIR_DISCOVERED"


def test_future_prices_cannot_change_signal_and_feature_type_rejects_future():
    past = {"timestamp": T0, "price": 1}
    future = {"timestamp": T0 + timedelta(seconds=30), "price": 2}
    with pytest.raises(ValueError):
        FeaturePriceHistory(T0, (future,))
    with pytest.raises(ValueError):
        FeaturePriceHistory(T0, ({"price": 1},))
    row = {"token": "token", "timestamp": T0.isoformat(),
           "feature_price_history": [past], "evaluation_price_path": [future],
           "market": {"score": 100, "liquidity": 100000}}
    first = run_backtest([row], strategy="market_only")
    row["evaluation_price_path"] = [dict(future, price=Decimal("0.001"))]
    second = run_backtest([row], strategy="market_only")
    assert first.signals == second.signals
    assert first.evaluations != second.evaluations
    assert generate_signal("t", T0, prices=[past, future]) == generate_signal(
        "t", T0, prices=[past, dict(future, price=999)])


AUDIT_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "release_audit.py"
spec = importlib.util.spec_from_file_location("release_audit", AUDIT_SCRIPT)
audit_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit_module)


@pytest.mark.parametrize("name", [".env", "x.pem", "x.key", ".git/config",
    "__pycache__/x.pyc", ".pytest_cache/x", ".ruff_cache/x", ".phase773-tmp/x",
    ".pytest-run-temp/x", "pytest-of-user/pytest-0/x", "local.sqlite", "backup.sql"])
def test_release_audit_detects_artifacts(tmp_path, name):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("")
    assert audit_module.audit(tmp_path)


def test_release_audit_credentials_redacted_and_cli_exit(tmp_path):
    secret = "synthetic-sensitive-value-123456"
    (tmp_path / "runtime.json").write_text('{"api_secret": "' + secret + '"}')
    result = subprocess.run([sys.executable, str(AUDIT_SCRIPT), str(tmp_path)],
                            check=False, capture_output=True, text=True)
    assert result.returncode == 1
    assert secret not in result.stdout + result.stderr
    assert "runtime.json" in result.stdout
    assert set(audit_module.audit(tmp_path)[0]) == {"path", "reason", "severity"}
    (tmp_path / "runtime.json").unlink()
    (tmp_path / ".env.example").write_text("API_KEY=\nENABLE_LIVE_TRADING=false\n")
    assert audit_module.audit(tmp_path) == []
    assert subprocess.run([sys.executable, str(AUDIT_SCRIPT), str(tmp_path)],
                          check=False, capture_output=True).returncode == 0


def test_release_audit_allows_runtime_settings_references(tmp_path):
    (tmp_path / "app.py").write_text(
        "client = Client(api_key=_runtime_settings.dexscreener_api_key.get_secret_value())\n"
    )
    assert audit_module.audit(tmp_path) == []


@pytest.mark.parametrize("content", [
    "-----BEGIN PRIVATE KEY-----\nsynthetic\n-----END PRIVATE KEY-----",
    'private_key="synthetic-key-value"',
    '{"mnemonic": "alpha beta gamma delta"}',
    'DATABASE_URL=postgresql://user:synthetic-password@localhost/db',
])
def test_release_audit_secret_formats(tmp_path, content):
    (tmp_path / "state.txt").write_text(content)
    findings = audit_module.audit(tmp_path)
    assert findings[0]["reason"] == "possible embedded credential"
    assert findings[0]["severity"] == "CRITICAL"


def test_feature_capability_copies_input_and_dataset_export_splits_prices():
    observation = {"timestamp": T0, "price": 1}
    history = FeaturePriceHistory(T0, (observation,))
    observation["timestamp"] = T0 + timedelta(days=1)
    assert history.observations[0]["timestamp"] == T0
    with pytest.raises(TypeError):
        history.observations[0]["price"] = 999
    with _session() as session:
        session.add(Token(address="launch", chain="bsc", launchpad="flap", first_seen_at=T0))
        session.add_all([TradeEvent(chain="bsc", token_address="launch", trader_address="w",
            tx_hash=f"tx-{i}", side="BUY", source="fixture", price=i+1,
            timestamp=T0 + timedelta(seconds=i*30)) for i in range(2)])
        session.commit()
        rows, _ = HistoricalDatasetBuilder(session).build(T0, T0)
        payload = rows[0].as_dict()
        assert "prices" not in payload and "future_prices" not in payload
        assert len(payload["feature_price_history"]) == len(payload["evaluation_price_path"]) == 1
        signal = run_backtest([payload]).signals[0]
        payload["evaluation_price_path"][0]["price"] = 9999
        assert run_backtest([payload]).signals[0] == signal

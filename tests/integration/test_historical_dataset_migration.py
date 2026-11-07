"""Legacy payload preservation and durable dataset reconstruction after restart."""

import json
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import MetaData, Table, create_engine, select
from sqlalchemy.orm import Session

from smms.backtest.dataset import load_materialized_dataset
from smms.db.models import BacktestDataset, BacktestDatasetRow, Pair, Token, TradeEvent
from smms.research.rebuild import rebuild_due

URL = os.getenv("SMMS_MIGRATION_TEST_DATABASE_URL")
ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(not URL, reason="explicit isolated PostgreSQL migration URL required")
def test_legacy_dataset_is_preserved_quarantined_and_rebuilt_after_restart(tmp_path):
    owner = create_engine(URL)
    assert owner.dialect.name == "postgresql"
    schema = "smms_dataset_" + uuid.uuid4().hex
    with owner.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    engine = create_engine(URL, connect_args={"options": f"-csearch_path={schema}"})
    env = dict(os.environ, DATABASE_URL=URL, PGOPTIONS=f"-csearch_path={schema}", ENABLE_LIVE_TRADING="false")

    def alembic(*args):
        return subprocess.run([sys.executable, "-m", "alembic", *args], cwd=ROOT, env=env,
                              capture_output=True, text=True, timeout=60, check=False)

    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    try:
        result = alembic("upgrade", "20261106_0040")
        assert result.returncode == 0, result.stderr
        with Session(engine) as session:
            session.add_all([
                Token(chain="bsc", address="token"),
                Pair(chain_id=56, address="pair", token0_address="token", token1_address="quote", creation_timestamp=t0),
                TradeEvent(chain="bsc", token_address="token", pair_address="pair", trader_address="wallet",
                           tx_hash="entry", timestamp=t0, side="BUY", price=999, price_usd=2, source="fixture"),
            ])
            session.commit()
        legacy_payload = {"token": "token", "timestamp": t0.isoformat(), "market": {"price": "999"}, "model_version": "dataset-v4"}
        with engine.begin() as connection:
            metadata = MetaData()
            datasets = Table("backtest_datasets", metadata, autoload_with=connection)
            rows = Table("backtest_dataset_rows", metadata, autoload_with=connection)
            dataset_id = connection.execute(datasets.insert().values(
                dataset_version="legacy-study", build_timestamp=t0, period_from=t0, period_to=t0,
                chain="bsc", config_hash="legacy", source_coverage_json={"price": 1}, report_json={"rows_generated": 1},
            ).returning(datasets.c.id)).scalar_one()
            connection.execute(rows.insert().values(dataset_id=dataset_id, token="token", signal_timestamp=t0,
                                                    discovery_timestamp=t0, payload_json=legacy_payload))
        result = alembic("upgrade", "head")
        assert result.returncode == 0, result.stderr
        cli_env = dict(env, DATABASE_URL=URL.replace("postgresql+psycopg2://", "postgresql+asyncpg://"), SMMS_TEST_SCHEMA=schema)
        output = tmp_path / "study.json"
        # asyncpg does not read libpq PGOPTIONS. Bind the actual CLI's engine to
        # this test-owned schema without adding a product-specific test switch.
        bootstrap = "import os; from sqlalchemy.ext.asyncio import create_async_engine; from smms.db import database; database._engine=create_async_engine(os.environ['DATABASE_URL'], connect_args={'server_settings': {'search_path': os.environ['SMMS_TEST_SCHEMA']}}); from smms.main import cli; cli()"
        command = [sys.executable, "-c", bootstrap, "backtest", "--dataset", "legacy-study", "--output", str(output)]
        refused_cli = subprocess.run(command, cwd=ROOT, env=cli_env, capture_output=True, text=True, timeout=30, check=False)
        assert refused_cli.returncode != 0 and "unavailable or rebuilding" in refused_cli.stderr and not output.exists()
        with Session(engine) as session:
            assert session.scalar(select(BacktestDataset)) is None
            assert session.scalar(select(BacktestDatasetRow)) is None
            old = session.scalar(select(BacktestDataset).execution_options(include_invalid_snapshots=True))
            assert old.id == dataset_id and old.snapshot_status == "INVALIDATED" and old.config_hash == "legacy"
            # Repair/enqueue without running jobs; prove the queue survives a new session.
            from smms.research.rebuild import _repair
            _repair(session, epoch=1, now=t0 + timedelta(days=1), batch_size=16)
            session.commit()
        with Session(engine) as session:
            rebuild_due(session, now=t0 + timedelta(days=1))
            session.commit()
        with Session(engine) as session:
            current = session.scalar(select(BacktestDataset))
            assert current.previous_snapshot_id == dataset_id and current.source_epoch == 1
            assert current.period_from == current.period_to == t0
            assert current.input_manifest["parameters"]["reconstruction_policy"] == "legacy-upgrade-to-dataset-v5"
            assert load_materialized_dataset(session, "legacy-study")[0]["market"]["price"] == "2"
            archived = session.scalar(select(BacktestDatasetRow).where(BacktestDatasetRow.dataset_id == dataset_id)
                                      .execution_options(include_invalid_snapshots=True))
            assert archived.payload_json == legacy_payload
            with pytest.raises(LookupError):
                load_materialized_dataset(session, dataset_id)
        rendered = subprocess.run(command, cwd=ROOT, env=cli_env, capture_output=True, text=True, timeout=30, check=False)
        assert rendered.returncode == 0, rendered.stderr
        exported = json.loads(output.read_text(encoding="utf-8"))
        assert exported["dataset"]["input_digest"] and exported["dataset"]["research_revision"] == 1
        assert exported["dataset"]["source_epoch"] == 1
        refused = alembic("downgrade", "20261106_0040")
        assert refused.returncode != 0 and "refusing lossy downgrade" in refused.stderr
        assert "20261106_0043" in alembic("current").stdout
    finally:
        engine.dispose()
        try:
            with owner.begin() as connection:
                connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        finally:
            owner.dispose()

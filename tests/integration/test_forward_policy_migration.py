"""Exercise real historical upgrades in an owned PostgreSQL schema."""

import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import MetaData, Table, create_engine, select, text
from sqlalchemy.orm import Session

from smms.db.models import (
    LiveSignalObservation,
    LiveSignalOutcome,
    MarketEvent,
    Pair,
    Token,
)
from smms.evaluation import FORWARD_EVALUATION_VERSION
from smms.radar.live_forward import evaluate_due

ROOT = Path(__file__).resolve().parents[2]
URL = os.getenv("SMMS_MIGRATION_TEST_DATABASE_URL")


@pytest.mark.skipif(not URL, reason="explicit isolated PostgreSQL migration URL required")
def test_forward_upgrade_preserves_legacy_values_and_refuses_lossy_downgrade():
    schema = "smms_forward_migration_" + uuid.uuid4().hex
    owner = create_engine(URL)
    assert owner.dialect.name == "postgresql"
    with owner.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    scoped = create_engine(URL, connect_args={"options": f"-csearch_path={schema}"})
    env = dict(os.environ, DATABASE_URL=URL, PGOPTIONS=f"-csearch_path={schema}",
               ENABLE_LIVE_TRADING="false")

    def alembic(*arguments):
        return subprocess.run([sys.executable, "-m", "alembic", *arguments],
                              cwd=ROOT, env=env, capture_output=True, text=True,
                              timeout=60, check=False)

    try:
        old = alembic("upgrade", "20260919_0034")
        assert old.returncode == 0, old.stderr
        t0 = datetime(2026, 1, 1, tzinfo=UTC)
        with Session(scoped) as session:
            token = Token(chain="bsc", address="migration-token")
            session.add(token)
            session.flush()
            # Seed the historical schema without sending future ORM columns.
            candidate_table = Table("radar_candidates", MetaData(), autoload_with=session.connection())
            candidate_id = session.execute(candidate_table.insert().values(
                token_id=token.id, discovered_at=t0, latest_seen_at=t0, discovery_source="DEX_PAIR",
                discovery_reason="fixture", status="DISCOVERED", stage="UNKNOWN", priority_score=0,
                features_json={}, priority_json={}, data_status="UNKNOWN", created_at=t0, updated_at=t0, revision=1,
            ).returning(candidate_table.c.id)).scalar_one()
            observation = LiveSignalObservation(
                candidate_id=candidate_id, token_id=token.id, chain="bsc",
                token_address=token.address, observed_at=t0, signal_class="HIGH_SIGNAL",
                price=1, sample_group="HIGH_SIGNAL", t0_snapshot_json={
                    "price": "1", "price_basis": "USD", "pair_address": "migration-pair",
                },
            )
            session.add_all([
                observation,
                Pair(chain_id=56, address="migration-pair", token0_address=token.address,
                     token1_address="quote", creation_timestamp=t0),
                MarketEvent(chain_id=56, event_type="SWAP", token_address=token.address,
                            pair_address="migration-pair", tx_hash="migration-endpoint",
                            timestamp=t0 + timedelta(seconds=30), price=Decimal("1.2"),
                            source="fixture", metadata_json={"price_basis": "USD"}),
            ])
            session.flush()
            observation_id = observation.id
            session.execute(text(
                "INSERT INTO live_signal_outcomes "
                "(observation_id, horizon, target_at, status, return_pct) "
                "VALUES (:id, '30s', :target, 'COMPLETE', 999)"
            ), {"id": observation_id, "target": t0 + timedelta(seconds=30)})
            session.commit()
        upgraded = alembic("upgrade", "20261105_0035")
        assert upgraded.returncode == 0, upgraded.stderr
        with scoped.connect() as connection:
            assert connection.execute(text("SELECT evaluation_version FROM live_signal_outcomes")).scalar_one() == "legacy-v1"
        current_schema = alembic("upgrade", "head")
        assert current_schema.returncode == 0, current_schema.stderr
        with Session(scoped) as session:
            legacy = session.scalar(select(LiveSignalOutcome))
            assert legacy.evaluation_version == "legacy-v1"
            assert legacy.return_pct == 999
            assert evaluate_due(session, at=t0 + timedelta(seconds=30)) == 1
            session.commit()
            rows = session.scalars(select(LiveSignalOutcome).where(
                LiveSignalOutcome.observation_id == observation_id,
                LiveSignalOutcome.horizon == "30s",
            )).all()
            assert {r.evaluation_version: r.return_pct for r in rows} == {
                "legacy-v1": Decimal(999), FORWARD_EVALUATION_VERSION: Decimal("0.2"),
            }
        refused = alembic("downgrade", "20260919_0034")
        assert refused.returncode != 0
        assert "Forward versions coexist" in refused.stderr
        current = alembic("current")
        assert current.returncode == 0
        assert "20261106_0041" in current.stdout
        with scoped.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM live_signal_outcomes "
                                           "WHERE horizon = '30s'")).scalar_one() == 2
    finally:
        scoped.dispose()
        try:
            with owner.begin() as connection:
                connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        finally:
            owner.dispose()

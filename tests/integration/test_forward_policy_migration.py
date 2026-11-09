"""Exercise real historical upgrades in an owned PostgreSQL schema."""

import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from migration_head import CURRENT_HEAD
from sqlalchemy import MetaData, Table, create_engine, select, text
from sqlalchemy.orm import Session

from smms.db.models import (
    LiveSignalObservation,
    LiveSignalOutcome,
    MarketEvent,
    Pair,
    RadarCandidateEvidence,
    Token,
)
from smms.evaluation import FORWARD_EVALUATION_VERSION
from smms.radar.live_forward import evaluate_due
from smms.research.rebuild import rebuild_due

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
            observation_table = Table("live_signal_observations", MetaData(), autoload_with=session.connection())
            observation_id = session.execute(observation_table.insert().values(
                candidate_id=candidate_id, token_id=token.id, chain="bsc",
                token_address=token.address, observed_at=t0, signal_class="HIGH_SIGNAL",
                feature_version="legacy", config_version="legacy", model_version="legacy", created_at=t0,
                price=1, sample_group="HIGH_SIGNAL", t0_snapshot_json={
                    "price": "1", "price_basis": "USD", "pair_address": "migration-pair",
                },
            ).returning(observation_table.c.id)).scalar_one()
            pair = Pair(chain_id=56, address="migration-pair", token0_address=token.address,
                        token1_address="quote", creation_timestamp=t0)
            session.add_all([
                pair,
                MarketEvent(chain_id=56, event_type="SWAP", token_address=token.address,
                            pair_address="migration-pair", tx_hash="migration-entry", timestamp=t0,
                            price=1, source="fixture", metadata_json={"price_basis": "USD"}),
                MarketEvent(chain_id=56, event_type="SWAP", token_address=token.address,
                            pair_address="migration-pair", tx_hash="migration-endpoint",
                            timestamp=t0 + timedelta(seconds=30), price=Decimal("1.2"),
                            source="fixture", metadata_json={"price_basis": "USD"}),
            ])
            session.flush()
            session.add(RadarCandidateEvidence(candidate_id=candidate_id, source_key=f"pair:{pair.id}",
                                                source_kind="PAIR_CREATED", pair_id=pair.id, raw_required=False, valid=True))
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
        with scoped.begin() as connection:
            outcomes = Table("live_signal_outcomes", MetaData(), autoload_with=connection)
            connection.execute(outcomes.insert().values(
                observation_id=observation_id, horizon="30s", target_at=t0 + timedelta(seconds=30),
                evaluation_version=FORWARD_EVALUATION_VERSION, status="COMPLETE", return_pct=888,
                next_evaluation_at=t0 + timedelta(seconds=90), evaluated_at=t0 + timedelta(seconds=35),
                endpoint_tolerance_seconds=5, provenance_json={"old": "unverified-result"},
            ))
        current_schema = alembic("upgrade", "head")
        assert current_schema.returncode == 0, current_schema.stderr
        with Session(scoped) as session:
            assert session.scalar(select(LiveSignalObservation)) is None
            assert session.scalar(select(LiveSignalOutcome)) is None
            legacy = session.scalar(select(LiveSignalOutcome).where(LiveSignalOutcome.evaluation_version == "legacy-v1")
                                    .execution_options(include_invalid_snapshots=True))
            assert legacy.evaluation_version == "legacy-v1"
            assert legacy.return_pct == 999
            from smms.research.rebuild import _repair
            _repair(session, epoch=1, now=t0 + timedelta(days=2), batch_size=16)
            session.commit()
        with Session(scoped) as session:
            for step in range(8):
                rebuild_due(session, now=t0 + timedelta(days=2, minutes=step))
                session.commit()
            assert evaluate_due(session, at=t0 + timedelta(seconds=35)) == 0  # recovered result repairs the retry job
            session.commit()
            current_observation = session.scalar(select(LiveSignalObservation))
            assert current_observation.previous_snapshot_id == observation_id
            assert current_observation.input_manifest["parameters"]["reconstruction_policy"] == "legacy-forward-upgrade-v1"
            rows = session.scalars(select(LiveSignalOutcome).where(
                LiveSignalOutcome.observation_root_id == observation_id,
                LiveSignalOutcome.horizon == "30s",
            ).execution_options(include_invalid_snapshots=True)).all()
            assert sorted(r.return_pct for r in rows) == [Decimal("0.2"), Decimal(888), Decimal(999)]
            current_result = session.scalar(select(LiveSignalOutcome))
            assert current_result.return_pct == Decimal("0.2") and current_result.observation_id == current_observation.id
            assert current_result.evaluated_at == t0 + timedelta(seconds=35)
        refused = alembic("downgrade", "20260919_0034")
        assert refused.returncode != 0
        assert "refusing lossy downgrade" in refused.stderr
        current = alembic("current")
        assert current.returncode == 0
        assert CURRENT_HEAD in current.stdout
        with scoped.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM live_signal_outcomes "
                                           "WHERE horizon = '30s'")).scalar_one() == 3
    finally:
        scoped.dispose()
        try:
            with owner.begin() as connection:
                connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        finally:
            owner.dispose()

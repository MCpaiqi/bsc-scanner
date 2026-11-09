"""Upgrade0044 preserves canonical state and refuses loss of event history."""

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from smms.db.models import RadarEventOutbox
from smms.radar.outbox import enqueue_event

URL = os.getenv("SMMS_MIGRATION_TEST_DATABASE_URL")
ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(not URL, reason="explicit isolated PostgreSQL migration URL required")
def test_outbox_migration_preserves_state_and_refuses_lossy_downgrade():
    owner = create_engine(URL)
    assert owner.dialect.name == "postgresql"
    schema = "smms_outbox_" + uuid.uuid4().hex
    with owner.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    engine = create_engine(URL, connect_args={"options": f"-csearch_path={schema}"})
    env = dict(os.environ, DATABASE_URL=URL, PGOPTIONS=f"-csearch_path={schema}", ENABLE_LIVE_TRADING="false")

    def alembic(*arguments):
        return subprocess.run([sys.executable, "-m", "alembic", *arguments], cwd=ROOT, env=env,
                              capture_output=True, text=True, timeout=60, check=False)

    try:
        before = alembic("upgrade", "20261106_0043")
        assert before.returncode == 0, before.stderr
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO research_states(chain,epoch) VALUES('bsc',7) "
                                    "ON CONFLICT(chain) DO UPDATE SET epoch=7"))
        after = alembic("upgrade", "head")
        assert after.returncode == 0, after.stderr
        assert {column["name"] for column in inspect(engine).get_columns("radar_event_outbox")} == set(
            RadarEventOutbox.__table__.columns.keys())
        empty_downgrade = alembic("downgrade", "20261106_0043")
        assert empty_downgrade.returncode == 0, empty_downgrade.stderr
        assert alembic("upgrade", "head").returncode == 0
        with Session(engine) as session:
            event = enqueue_event(session, "CANDIDATE_UPDATED", chain="bsc", token="fixture", data={"epoch": 7})
            event_id = event.event_id
            session.commit()
        refused = alembic("downgrade", "20261106_0043")
        assert refused.returncode != 0 and "cannot discard committed event/delivery history" in refused.stderr
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT event_id FROM radar_event_outbox")) == event_id
            assert connection.scalar(text("SELECT epoch FROM research_states WHERE chain='bsc'")) == 7
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20261107_0045"
    finally:
        engine.dispose()
        with owner.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        owner.dispose()

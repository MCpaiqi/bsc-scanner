"""Historical Alpha rows survive the real PostgreSQL coverage migration."""

import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from smms.alpha.work import register_pair
from smms.db.models import AlphaSnapshot, Pair

ROOT = Path(__file__).resolve().parents[2]
URL = os.getenv("SMMS_MIGRATION_TEST_DATABASE_URL")


@pytest.mark.skipif(not URL, reason="explicit isolated PostgreSQL migration URL required")
def test_alpha_upgrade_preserves_legacy_and_refuses_lossy_downgrade():
    schema = "smms_alpha_migration_" + uuid.uuid4().hex
    owner = create_engine(URL)
    assert owner.dialect.name == "postgresql"
    with owner.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    scoped = create_engine(URL, connect_args={"options": f"-csearch_path={schema}"})
    env = dict(os.environ, DATABASE_URL=URL, PGOPTIONS=f"-csearch_path={schema}", ENABLE_LIVE_TRADING="false")

    def alembic(*arguments):
        return subprocess.run([sys.executable, "-m", "alembic", *arguments], cwd=ROOT, env=env,
                              capture_output=True, text=True, timeout=60, check=False)

    try:
        old = alembic("upgrade", "20261105_0035")
        assert old.returncode == 0, old.stderr
        with scoped.begin() as connection:
            connection.execute(text(
                "INSERT INTO alpha_snapshots (chain, token_address, pair_address, pair_created_at, snapshot_at, "
                "age_seconds, observation_seconds, alpha_score, score_version, scored_at, features_json, "
                "components_json, positive_reasons, negative_reasons, warnings, outcome_json, updated_at) "
                "VALUES ('bsc','legacy-token','legacy-pair',now(),now(),60,60,99,'experimental-v2',now(), "
                "'{}','{}','[]','[]','[]',:outcome,now())"
            ), {"outcome": '{"reached_10x":false}'})
        upgraded = alembic("upgrade", "head")
        assert upgraded.returncode == 0, upgraded.stderr
        with Session(scoped) as session:
            legacy = session.scalar(select(AlphaSnapshot))
            assert legacy.alpha_score == 99 and legacy.score_version == "experimental-v2"
            assert legacy.task_status == "LEGACY" and legacy.outcome_json["reached_10x"] is False
            p = Pair(chain_id=56, address="new-pair", token0_address="token", token1_address="quote",
                     quote_token="quote", creation_timestamp=datetime(2026, 1, 1, tzinfo=UTC))
            session.add(p)
            session.flush()
            register_pair(session, p)
            session.commit()
            pending = session.scalars(select(AlphaSnapshot).where(AlphaSnapshot.task_status == "PENDING_INPUT")).all()
            assert len(pending) == 4 and all(row.alpha_score is None for row in pending)
        refused = alembic("downgrade", "20261105_0035")
        assert refused.returncode != 0 and "Alpha coverage work exists" in refused.stderr
        assert "20261106_0043" in alembic("current").stdout
    finally:
        scoped.dispose()
        try:
            with owner.begin() as connection:
                connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        finally:
            owner.dispose()

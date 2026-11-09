"""Archiving must be additive and cannot be downgraded into missing fences."""

import hashlib
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from migration_head import CURRENT_HEAD
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

URL = os.getenv("SMMS_MIGRATION_TEST_DATABASE_URL")
ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(not URL, reason="explicit isolated PostgreSQL migration URL required")
def test_anchor_archive_upgrade_and_guarded_downgrade():
    from smms.alpha.anchors import anchor_blocks, archive_anchors
    from smms.db.models import AlphaBlockAnchor

    T0 = datetime(2026, 1, 1, tzinfo=UTC)

    schema = "smms_anchor_archive_" + uuid.uuid4().hex
    owner = create_engine(URL)
    assert owner.dialect.name == "postgresql"
    with owner.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    scoped = create_engine(URL, connect_args={"options": f"-csearch_path={schema}"})
    environment = dict(os.environ, DATABASE_URL=URL, PGOPTIONS=f"-csearch_path={schema}", ENABLE_LIVE_TRADING="false")
    def alembic(*arguments):
        return subprocess.run([sys.executable, "-m", "alembic", *arguments], cwd=ROOT, env=environment,
                              capture_output=True, text=True, timeout=60, check=False)
    try:
        old = alembic("upgrade", "20261107_0045")
        assert old.returncode == 0, old.stderr
        with Session(scoped) as session:
            original = {n: hashlib.sha256(str(n).encode()).hexdigest() for n in range(1536)}
            session.add_all([AlphaBlockAnchor(chain_id=56, block_number=n, block_hash=value, observed_at=T0)
                             for n, value in original.items()])
            session.commit()
        upgrade = alembic("upgrade", "head")
        assert upgrade.returncode == 0, upgrade.stderr
        assert "canonical_anchor_segments" in inspect(scoped).get_table_names()
        assert alembic("downgrade", "20261107_0045").returncode == 0
        assert alembic("upgrade", "head").returncode == 0
        with Session(scoped) as session:
            assert archive_anchors(session, keep_recent_blocks=512, now=T0)["rows_archived"] == 512
            session.commit()
        refused = alembic("downgrade", "20261107_0045")
        assert refused.returncode != 0 and "canonical anchor archive" in refused.stderr
        assert CURRENT_HEAD in alembic("current").stdout
        offline = alembic("downgrade", "20261109_0046:20261107_0045", "--sql")
        assert offline.returncode != 0 and "online" in offline.stderr
        with Session(scoped) as session:
            assert anchor_blocks(session, {1: "stale"}, observed_at=T0, authoritative=False)[0][1] == original[1]
    finally:
        scoped.dispose()
        with owner.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        owner.dispose()

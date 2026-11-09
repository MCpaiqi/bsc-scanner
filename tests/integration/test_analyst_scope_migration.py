"""Upgrade legacy analyst data without inferring scope or losing write guards."""

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from smms.db.models import AlertRule, RadarAnnotation
from smms.radar.alerts import create_rule

URL = os.getenv("SMMS_MIGRATION_TEST_DATABASE_URL")
ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(not URL, reason="explicit isolated PostgreSQL migration URL required")
def test_analyst_scope_upgrade_preserves_legacy_and_refuses_lossy_downgrade():
    owner = create_engine(URL)
    assert owner.dialect.name == "postgresql"
    schema = "smms_analyst_" + uuid.uuid4().hex
    with owner.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    engine = create_engine(URL, connect_args={"options": f"-csearch_path={schema}"})
    env = dict(os.environ, DATABASE_URL=URL, PGOPTIONS=f"-csearch_path={schema}", ENABLE_LIVE_TRADING="false")
    def alembic(*arguments):
        return subprocess.run([sys.executable, "-m", "alembic", *arguments], cwd=ROOT, env=env,
            capture_output=True, text=True, timeout=60, check=False)
    try:
        old = alembic("upgrade", "20261107_0044")
        assert old.returncode == 0, old.stderr
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO radar_annotations(chain,token_address,favorite,note,tags,created_at,updated_at) "
                "VALUES('bsc','legacy',true,'preserve me','[]',now(),now())"))
            connection.execute(text("INSERT INTO alert_rules(name,chain,conditions_json,match_mode,notification_channels,cooldown_seconds,enabled,created_at,updated_at) "
                "VALUES('legacy 0x-token name','bsc','[]','all','[\"in-app\"]',300,true,now(),now())"))
        upgraded = alembic("upgrade", "head")
        assert upgraded.returncode == 0, upgraded.stderr
        assert {"token_address", "request_id"} <= {column["name"] for column in inspect(engine).get_columns("alert_rules")}
        with Session(engine) as session:
            annotation = session.query(RadarAnnotation).one()
            assert annotation.revision == 1 and not annotation.deleted and annotation.note == "preserve me"
            legacy = session.query(AlertRule).one()
            assert legacy.scope == "GLOBAL" and legacy.token_address is None and legacy.request_id is None
        safe = alembic("downgrade", "20261107_0044")
        assert safe.returncode == 0, safe.stderr
        assert alembic("upgrade", "head").returncode == 0
        for kind in ["target", "revision", "request", "deleted"]:
            with Session(engine) as session:
                if kind in {"target", "request"}:
                    rule = create_rule(session, {"name": "new", "chain": "bsc", "scope": "TOKEN" if kind == "target" else "GLOBAL",
                        "token_address": "0x" + "a" * 40 if kind == "target" else None,
                        "request_id": str(uuid.uuid4()) if kind == "request" else None,
                        "conditions": [{"field": "smart_money_score", "operator": ">=", "value": 60}]})
                    new_id = rule.id
                else:
                    session.execute(text("UPDATE radar_annotations SET revision=:revision,deleted=:deleted"),
                        {"revision": 2 if kind == "revision" else 1, "deleted": kind == "deleted"})
                session.commit()
            refused = alembic("downgrade", "20261107_0044")
            assert refused.returncode != 0 and "cannot discard analyst scope/revision metadata" in refused.stderr
            with engine.begin() as connection:
                assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20261107_0045"
                assert connection.scalar(text("SELECT note FROM radar_annotations")) == "preserve me"
                if kind in {"target", "request"}:
                    connection.execute(text("DELETE FROM alert_rules WHERE id=:id"), {"id": new_id})
                else:
                    connection.execute(text("UPDATE radar_annotations SET revision=1,deleted=false"))
    finally:
        engine.dispose()
        with owner.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        owner.dispose()

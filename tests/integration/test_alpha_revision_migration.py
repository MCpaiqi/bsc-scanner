"""Real historical upgrade preserves scores and reconstructs fresh revisions."""

import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from smms.alpha.live import process_live_alpha
from smms.alpha.work import SWAP_TOPIC, register_pair
from smms.db.models import (
    AlphaCoverageJob,
    AlphaCoverageReceipt,
    AlphaSnapshot,
    Pair,
    Token,
    TradeEvent,
)

ROOT = Path(__file__).resolve().parents[2]
URL = os.getenv("SMMS_MIGRATION_TEST_DATABASE_URL")


@pytest.mark.skipif(not URL, reason="explicit isolated PostgreSQL migration URL required")
def test_alpha_revision_upgrade_preserves_inputs_and_requires_new_coverage():
    schema = "smms_alpha_revision_" + uuid.uuid4().hex
    owner = create_engine(URL)
    assert owner.dialect.name == "postgresql"
    with owner.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    scoped = create_engine(URL, connect_args={"options": f"-csearch_path={schema}"})
    env = dict(os.environ, DATABASE_URL=URL, PGOPTIONS=f"-csearch_path={schema}", ENABLE_LIVE_TRADING="false")

    def alembic(*arguments):
        return subprocess.run([sys.executable, "-m", "alembic", *arguments], cwd=ROOT, env=env,
                              capture_output=True, text=True, timeout=60, check=False)

    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    now = t0 + timedelta(days=2)
    try:
        old = alembic("upgrade", "20261106_0036")
        assert old.returncode == 0, old.stderr
        with Session(scoped) as session:
            p = Pair(chain_id=56, address="revision-pair", token0_address="revision-token", token1_address="quote",
                     quote_token="quote", creation_timestamp=t0, creation_block=100, creation_block_hash="creation")
            session.add_all([p, Token(chain="bsc", address="revision-token", decimals=18),
                             Token(chain="bsc", address="quote", decimals=18)])
            session.flush()
            job = AlphaCoverageJob(pair_id=p.id, generation=0, next_block=401,
                                   target_at=t0 + timedelta(seconds=3900), next_attempt_at=t0, status="COMPLETE")
            session.add(job)
            session.flush()
            receipt_values = {"start_block": 100, "end_block": 400, "start_hash": "creation", "end_hash": "end",
                              "start_parent_hash": "parent", "start_at": t0, "end_at": t0 + timedelta(seconds=4000),
                              "raw_count": 1, "decoded_count": 1, "completed_at": t0 + timedelta(seconds=4000),
                              "query_json": {"chain_id": 56, "pair": p.address, "topic": SWAP_TOPIC,
                                             "protocol": "pancakeswap", "confirmed": True}}
            session.add(AlphaCoverageReceipt(job_id=job.id, generation=0, **receipt_values))
            session.add(TradeEvent(chain="bsc", token_address="revision-token", pair_address=p.address,
                                   trader_address="buyer", tx_hash="entry", block_hash="trade-block",
                                   block_number=110, timestamp=t0 + timedelta(seconds=10), side="BUY",
                                   quote_amount=2, token_amount=1, price_quote=2, source="fixture"))
            session.execute(text(
                "INSERT INTO alpha_snapshots (chain, token_address, pair_address, pair_created_at, snapshot_at, "
                "age_seconds, observation_seconds, alpha_score, score_version, scored_at, features_json, "
                "components_json, positive_reasons, negative_reasons, warnings, outcome_json, updated_at, "
                "task_status, coverage_generation) VALUES ('bsc','revision-token','revision-pair',:t0,:at,"
                "60,60,99,'experimental-v3',:now,'{}','{}','[]','[]','[]',:outcome,:now,'COMPLETE',0)"
            ), {"t0": t0, "at": t0 + timedelta(seconds=60), "now": now,
                "outcome": '{"reached_10x":false,"minimum_quote":"0.01"}'})
            session.commit()
            job_id, pair_id = job.id, p.id
        upgraded = alembic("upgrade", "head")
        assert upgraded.returncode == 0, upgraded.stderr
        with Session(scoped) as session:
            legacy = session.scalar(select(AlphaSnapshot))
            assert legacy.input_revision == 0 and legacy.alpha_score == 99 and legacy.task_status == "INVALIDATED"
            job = session.get(AlphaCoverageJob, job_id)
            assert job.generation == 1 and job.next_block == 100 and job.status == "PENDING"
            assert session.scalar(select(AlphaCoverageReceipt)).canonical is False
            p = session.get(Pair, pair_id)
            register_pair(session, p)
            assert process_live_alpha(session, now=now) == 0
            pending = session.scalar(select(AlphaSnapshot).where(AlphaSnapshot.supersedes_id == legacy.id))
            assert pending.task_status == "PENDING_INPUT" and pending.alpha_score is None
            session.add(AlphaCoverageReceipt(job_id=job_id, generation=1, **receipt_values))
            process_live_alpha(session, now=now + timedelta(seconds=61))
            session.commit()
            assert pending.input_revision == 1 and pending.input_manifest["trade_ids"]
            assert pending.task_status == "COMPLETE" and legacy.alpha_score == 99
        refused = alembic("downgrade", "20261106_0036")
        assert refused.returncode != 0 and "Alpha research revisions exist" in refused.stderr
        assert "20261106_0041" in alembic("current").stdout
    finally:
        scoped.dispose()
        try:
            with owner.begin() as connection:
                connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        finally:
            owner.dispose()

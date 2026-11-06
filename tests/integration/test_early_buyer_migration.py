"""Real legacy cache preservation, quarantine and restartable reconstruction."""

import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import MetaData, Table, create_engine, select
from sqlalchemy.orm import Session

from smms.db.models import EarlyBuyer, Pair, Token, TradeEvent, WalletTokenPosition
from smms.research.rebuild import rebuild_due

URL = os.getenv("SMMS_MIGRATION_TEST_DATABASE_URL")
ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(not URL, reason="explicit isolated PostgreSQL migration URL required")
def test_legacy_buyers_and_positions_survive_and_rebuild_after_restart():
    owner = create_engine(URL)
    assert owner.dialect.name == "postgresql"
    schema = "smms_early_" + uuid.uuid4().hex
    with owner.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    engine = create_engine(URL, connect_args={"options": f"-csearch_path={schema}"})
    env = dict(os.environ, DATABASE_URL=URL, PGOPTIONS=f"-csearch_path={schema}", ENABLE_LIVE_TRADING="false")

    def alembic(*args):
        return subprocess.run([sys.executable, "-m", "alembic", *args], cwd=ROOT, env=env,
                              capture_output=True, text=True, timeout=60, check=False)

    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    cutoff = t0 + timedelta(seconds=20)
    try:
        result = alembic("upgrade", "20261106_0038")
        assert result.returncode == 0, result.stderr
        with Session(engine) as session:
            session.add_all([Token(chain="bsc", address="token"), Token(chain="bsc", address="quote"),
                             Pair(chain_id=56, address="pair", token0_address="token", token1_address="quote",
                                  creation_timestamp=t0, creation_block=100),
                             TradeEvent(chain="bsc", token_address="token", pair_address="pair", trader_address="wallet",
                                        tx_hash="canonical", timestamp=cutoff, block_number=101, side="BUY",
                                        token_amount=2, value_usd=10, source="fixture")])
            session.commit()
        with engine.begin() as connection:
            metadata = MetaData()
            buyers = Table("early_buyers", metadata, autoload_with=connection)
            connection.execute(buyers.insert().values(
                chain="bsc", token_address="token", wallet_address="wallet", first_buy_tx="old-orphan",
                first_buy_timestamp=cutoff, buy_rank=1, buy_count=99, market_cap_status="KNOWN",
                window_type="buyers", window_value=20, metadata_json={},
            ))
            positions = Table("wallet_token_positions", metadata, autoload_with=connection)
            connection.execute(positions.insert().values(
                chain="bsc", token_address="token", wallet_address="wallet", cumulative_bought=999,
                cumulative_sold=0, current_balance=999, last_trade_timestamp=cutoff,
                is_reorg_corrected=False, metadata_json={},
            ))
        result = alembic("upgrade", "head")
        assert result.returncode == 0, result.stderr
        with Session(engine) as session:
            assert session.scalar(select(EarlyBuyer)) is None
            assert session.scalar(select(WalletTokenPosition)) is None
            rebuild_due(session, now=cutoff + timedelta(days=1), batch_size=1)
            session.commit()
        # A fresh Session represents worker restart with pending durable work.
        with Session(engine) as session:
            for _ in range(4):
                rebuild_due(session, now=cutoff + timedelta(days=1), batch_size=1)
                session.commit()
            buyer = session.scalar(select(EarlyBuyer))
            position = session.scalar(select(WalletTokenPosition))
            assert buyer.first_buy_tx == "canonical" and buyer.buy_count == 1
            assert position.current_balance == 2 and position.current_price == 5
            assert buyer.input_manifest["parameters"]["reconstruction_policy"] == "legacy-defaults-v1"
            legacy = session.scalar(select(WalletTokenPosition).where(WalletTokenPosition.research_revision == 0)
                                     .execution_options(include_invalid_snapshots=True))
            assert legacy.current_balance == 999 and legacy.snapshot_status == "SUPERSEDED"
        refused = alembic("downgrade", "20261106_0038")
        assert refused.returncode != 0 and "refusing lossy downgrade" in refused.stderr
        assert "20261106_0039" in alembic("current").stdout
    finally:
        engine.dispose()
        try:
            with owner.begin() as connection:
                connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        finally:
            owner.dispose()

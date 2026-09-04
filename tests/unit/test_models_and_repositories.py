from datetime import UTC, datetime

from sqlalchemy import create_engine, event, inspect
from sqlalchemy.orm import Session

from smms.db.models import (
    Base,
    Signal,
    Token,
    Wallet,
    WalletScore,
    WalletTransaction,
)
from smms.db.repositories import TokenRepository, WalletRepository, WalletTransactionRepository


def test_phase_one_schema_contains_required_tables() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    table_names = set(inspect(engine).get_table_names())
    assert {
        "tokens",
        "wallets",
        "wallet_transactions",
        "token_wallet_events",
        "wallet_scores",
        "token_scores",
        "signals",
        "paper_trades",
        "raw_chain_records",
        "ingestion_runs",
    } <= table_names


def test_token_and_wallet_repositories_upsert_by_chain_address() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        tokens = TokenRepository(session)
        wallets = WalletRepository(session)
        token = tokens.upsert(address="Token111", symbol="TEST", name="Test", chain="solana")
        same_token = tokens.upsert(address="Token111", symbol="UPDATED", chain="solana")
        wallet = wallets.upsert(address="Wallet111", chain="solana")
        same_wallet = wallets.upsert(address="Wallet111", chain="solana", smart_money_score=88)
        session.commit()

        assert same_token.id == token.id
        assert tokens.get_by_address("Token111", "solana").symbol == "UPDATED"
        assert same_wallet.id == wallet.id
        assert wallets.get_by_address("Wallet111", "solana").smart_money_score == 88


def test_wallet_transaction_insert_is_idempotent_by_wallet_signature() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        wallet = Wallet(address="Wallet111", chain="solana")
        token = Token(address="Token111", chain="solana")
        session.add_all([wallet, token])
        session.flush()
        repo = WalletTransactionRepository(session)
        timestamp = datetime(2025, 1, 1, tzinfo=UTC)
        first = repo.add_idempotent(
            wallet_id=wallet.id,
            signature="sig-1",
            timestamp=timestamp,
            side="BUY",
            token_out="Token111",
            amount_out_raw=1000,
            amount_out=1000,
        )
        second = repo.add_idempotent(
            wallet_id=wallet.id,
            signature="sig-1",
            timestamp=timestamp,
            side="BUY",
        )
        session.commit()
        assert second.id == first.id
        assert session.query(WalletTransaction).count() == 1


def test_score_and_signal_versions_are_persistable() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        token = Token(address="Token111", chain="solana")
        wallet = Wallet(address="Wallet111", chain="solana")
        session.add_all([token, wallet])
        session.flush()
        session.add(
            WalletScore(wallet_id=wallet.id, total_score=91, version="v1")
        )
        session.add(
            Signal(
                token_id=token.id,
                created_at=datetime.now(UTC),
                signal_type="WATCH",
                signal_strength=62,
                status="OPEN",
            )
        )
        session.commit()
        assert session.query(WalletScore).one().version == "v1"
        assert session.query(Signal).one().signal_type == "WATCH"


def test_raw_chain_records_have_provider_scoped_identity() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        from smms.db.models import RawChainRecord

        session.add(
            RawChainRecord(
                provider="fixture",
                record_type="transaction",
                external_id="sig-1",
                payload_json={"slot": 1},
            )
        )
        session.commit()
        duplicate = RawChainRecord(
            provider="fixture",
            record_type="transaction",
            external_id="sig-1",
            payload_json={"slot": 1},
        )
        session.add(duplicate)
        from sqlalchemy.exc import IntegrityError

        try:
            session.commit()
        except IntegrityError:
            session.rollback()
        assert session.query(RawChainRecord).count() == 1


def test_database_cascade_removes_wallet_children() -> None:
    engine = create_engine("sqlite://")
    event.listen(
        engine,
        "connect",
        lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"),
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        wallet = Wallet(address="Wallet111", chain="solana")
        session.add(wallet)
        session.flush()
        session.add(
            WalletTransaction(
                wallet_id=wallet.id,
                signature="sig-1",
                timestamp=datetime(2025, 1, 1, tzinfo=UTC),
            )
        )
        session.commit()
        session.delete(wallet)
        session.commit()
        assert session.query(WalletTransaction).count() == 0

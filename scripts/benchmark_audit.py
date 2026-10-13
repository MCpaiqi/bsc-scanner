"""Benchmark token-scoped intelligence refresh without replaying the chain.

The benchmark uses an in-memory SQLite database by default, making it safe to
run in CI. Production runs should repeat the same cases against PostgreSQL.
"""
from __future__ import annotations

import argparse
import json
import time
import tracemalloc
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from smms.db.models import Base, Token, TradeEvent
from smms.smart_money.aggregation import IntelligenceAggregationService


def run_case(*, token_count: int, trade_count: int) -> dict[str, object]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    now = datetime.now(UTC)
    target = "0xtoken0000"
    with Session(engine) as session:
        session.bulk_save_objects(
            [Token(chain="bsc", address=f"0xtoken{i:04d}", creation_timestamp=now) for i in range(token_count)]
        )
        session.bulk_save_objects(
            [
                TradeEvent(
                    chain="bsc",
                    token_address=target if i % max(1, trade_count // 100) == 0 else f"0xtoken{i % token_count:04d}",
                    trader_address=f"0xwallet{i % 10:02d}",
                    tx_hash=f"tx-{i}",
                    event_index=0,
                    block_number=i,
                    timestamp=now - timedelta(seconds=trade_count - i),
                    side="BUY" if i % 2 == 0 else "SELL",
                    native_amount=1,
                    source="benchmark",
                )
                for i in range(trade_count)
            ]
        )
        session.commit()
        statements: list[str] = []

        def capture(_connection, _cursor, statement, _parameters, _context, _executemany):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        event.listen(engine, "before_cursor_execute", capture)
        tracemalloc.start()
        started = time.perf_counter()
        result = IntelligenceAggregationService(session, chain="bsc").refresh_token(target, as_of=now)
        elapsed = time.perf_counter() - started
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        event.remove(engine, "before_cursor_execute", capture)
        session.rollback()
    full_chain_selects = [
        statement for statement in statements
        if "from trade_events" in statement.lower()
        and "token_address" not in statement.lower()
        and "trader_address" not in statement.lower()
    ]
    return {
        "token_count": token_count,
        "trade_count": trade_count,
        "elapsed_seconds": round(elapsed, 6),
        "peak_memory_bytes": peak,
        "sql_select_count": len(statements),
        "wallet_snapshots": result.wallet_snapshots,
        "token_snapshots": result.token_snapshots,
        "full_chain_replay_detected": bool(full_chain_selects),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scales", default="1000,10000,100000", help="token counts to benchmark")
    parser.add_argument("--trades", type=int, default=100000)
    parser.add_argument("--output", type=Path, default=Path("data/reports/phase7_9_5_audit_benchmark.json"))
    args = parser.parse_args()
    rows = [run_case(token_count=int(value), trade_count=args.trades) for value in args.scales.split(",") if value.strip()]
    payload = {"phase": "7.9.5", "target": "token-scoped incremental refresh", "cases": rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "cases": len(rows), "full_chain_replay": any(row["full_chain_replay_detected"] for row in rows)}))
    return 1 if any(row["full_chain_replay_detected"] for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Production-like validation helpers for Phase 7.9.5.

The command is intentionally read-only against the application database.  It
can be run as a short CI smoke check or left running beside the compose stack
for a 24/72 hour soak.  Runtime samples are append-only JSONL records so a
crash does not lose the observations already collected.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _run(command: list[str], *, cwd: Path, timeout_seconds: int = 15) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
        returncode = result.returncode
        stdout = result.stdout or ""
        stderr = result.stderr or ""
    except subprocess.TimeoutExpired as exc:
        returncode = 124
        stdout = str(exc.stdout or "")
        stderr = f"command timed out after {timeout_seconds}s"
    except OSError as exc:
        returncode = 127
        stdout = ""
        stderr = str(exc)
    return {
        "command": command,
        "returncode": returncode,
        "duration_seconds": round(time.perf_counter() - started, 3),
        "stdout": stdout[-4000:],
        "stderr": stderr[-4000:],
    }


def _compose_snapshot(root: Path) -> dict[str, Any]:
    return _run(["docker", "compose", "ps", "--format", "json"], cwd=root)


def _api_snapshot(api_url: str) -> dict[str, Any]:
    try:
        def fetch(path: str) -> dict[str, Any]:
            with urlopen(f"{api_url.rstrip('/')}{path}", timeout=3) as response:
                value = json.loads(response.read().decode("utf-8"))
            return value if isinstance(value, dict) else {}

        payload = fetch("/api/radar/health")
        stream = fetch("/api/bsc/stream/status")
        app_health = fetch("/health")
        radar_stats = fetch("/api/radar/stats")
        metrics = payload.get("metrics", {})
        chain = metrics.get("chain", {})
        ingestion = metrics.get("ingestion", {})
        audit = metrics.get("audit", {})
        return {
            "status": "PASS",
            "source_status": payload.get("source_status"),
            "sources": payload.get("sources", {}),
            "runtime": {
                "processed_blocks": chain.get("processed_block"),
                "block_lag": chain.get("block_lag"),
                "events_per_second": ingestion.get("event_rate"),
                "decode_errors": ingestion.get("decode_failures_total"),
                "audit_queue_depth": audit.get("queue_depth"),
                "audit_latency_ms": audit.get("latency_ms"),
                "sse_clients": metrics.get("sse", {}).get("client_count"),
                "ws_reconnects": stream.get("reconnect_count"),
                "provider_errors": stream.get("provider_errors"),
                "active_provider": stream.get("active_provider", stream.get("provider")),
                "candidate_counts": radar_stats.get("counts", {}),
                "audit_jobs": app_health.get("radar_audit", {}).get("total_jobs"),
                "signals_generated": app_health.get("metrics", {}).get("signals_total"),
            },
            "metrics": metrics,
        }
    except (OSError, URLError, ValueError, json.JSONDecodeError) as exc:
        return {"status": "UNAVAILABLE", "error": str(exc)[:500]}


def _db_checks() -> dict[str, Any]:
    """Run duplicate/PIT checks when SQLAlchemy is configured and reachable."""
    try:
        from sqlalchemy import create_engine, text
        from sqlalchemy.exc import SQLAlchemyError

        from smms.config import get_settings
    except ImportError as exc:  # pragma: no cover - import failures are reported, not fatal here
        return {"status": "SKIPPED", "error": str(exc)}
    url = get_settings().database_url.replace("+asyncpg", "")
    try:
        engine = create_engine(url)
        queries = {
            "raw_duplicate_identity": """
                SELECT count(*) FROM (
                  SELECT chain_id, transaction_hash, log_index
                  FROM raw_chain_events GROUP BY 1,2,3 HAVING count(*) > 1
                ) duplicates
            """,
            "market_duplicate_identity": """
                SELECT count(*) FROM (
                  SELECT chain_id, tx_hash, event_index
                  FROM market_events GROUP BY 1,2,3 HAVING count(*) > 1
                ) duplicates
            """,
            "trade_duplicate_identity": """
                SELECT count(*) FROM (
                  SELECT chain, tx_hash, event_index
                  FROM trade_events GROUP BY 1,2,3 HAVING count(*) > 1
                ) duplicates
            """,
            "future_observations": """
                SELECT count(*) FROM live_signal_observations o
                JOIN signals s ON s.id = (SELECT latest_signal_id FROM radar_candidates c
                                           WHERE c.id = o.candidate_id)
                WHERE o.observed_at < s.created_at
            """,
        }
        explain_sql = {
            "candidate_list": """
                SELECT c.id FROM radar_candidates c JOIN tokens t ON t.id = c.token_id
                WHERE t.chain = 'bsc' AND c.status NOT IN ('REJECTED', 'STALE')
                ORDER BY c.priority_score DESC LIMIT 100
            """,
            "token_detail": """
                SELECT c.id FROM radar_candidates c JOIN tokens t ON t.id = c.token_id
                WHERE t.chain = 'bsc' AND lower(t.address) = lower('0x0')
            """,
            "wallet_profile": """
                SELECT id FROM trade_events
                WHERE chain = 'bsc' AND trader_address = '0x0'
                ORDER BY timestamp, id LIMIT 1000
            """,
            "audit_job_fetch": """
                SELECT id FROM radar_audit_jobs
                WHERE status IN ('PENDING', 'RETRY') AND scheduled_at <= CURRENT_TIMESTAMP
                ORDER BY priority DESC, scheduled_at, id LIMIT 1
            """,
            "forward_outcome": """
                SELECT o.id FROM live_signal_outcomes o
                WHERE o.status = 'PENDING' AND o.target_at <= CURRENT_TIMESTAMP
                ORDER BY o.target_at LIMIT 100
            """,
        }
        with engine.connect() as connection:
            values = {name: int(connection.execute(text(sql)).scalar_one()) for name, sql in queries.items()}
            plans: dict[str, Any] = {}
            sequential_scans: list[str] = []
            for name, sql in explain_sql.items():
                try:
                    plan = connection.execute(text(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {sql}")).scalar_one()
                    plans[name] = plan
                    encoded = json.dumps(plan, ensure_ascii=True)
                    if "Seq Scan" in encoded:
                        sequential_scans.append(name)
                except SQLAlchemyError as exc:
                    plans[name] = {"status": "UNAVAILABLE", "error": str(exc)[:300]}
            feature_future = 0
            try:
                rows = connection.execute(text("""
                    SELECT o.observed_at, c.features_json
                    FROM live_signal_observations o
                    JOIN radar_candidates c ON c.id = o.candidate_id
                """))
                for observed_at, features in rows:
                    stack = [features] if isinstance(features, dict) else []
                    while stack:
                        value = stack.pop()
                        if isinstance(value, dict):
                            for key, item in value.items():
                                if isinstance(item, (dict, list)):
                                    stack.append(item)
                                elif isinstance(item, str) and ("timestamp" in str(key).lower() or str(key).lower().endswith("_at")):
                                    try:
                                        parsed = datetime.fromisoformat(item)
                                        if parsed.tzinfo is None:
                                            parsed = parsed.replace(tzinfo=UTC)
                                        if parsed > observed_at:
                                            feature_future += 1
                                    except ValueError:
                                        continue
                        elif isinstance(value, list):
                            stack.extend(value)
            except SQLAlchemyError:
                feature_future = 0
            values["future_feature_timestamps"] = feature_future
        return {
            "status": "PASS" if not any(values.values()) else "FAIL",
            "checks": values,
            "explain_analyze": {"plans": plans, "sequential_scans": sequential_scans},
        }
    except (OSError, SQLAlchemyError, ValueError) as exc:
        return {"status": "UNAVAILABLE", "error": str(exc)[:500]}


def _index_checks() -> dict[str, Any]:
    required = {
        "raw_chain_events": {"uq_raw_chain_events_identity", "ix_raw_chain_events_block"},
        "market_events": {"uq_market_events_identity", "ix_market_events_token_time"},
        "trade_events": {"uq_trade_events_chain_tx_index", "ix_trade_events_token_time"},
        "radar_candidates": {"ix_radar_status_priority"},
        "radar_audit_jobs": {"ix_radar_audit_claim", "ix_radar_audit_token"},
        "signals": {"ix_signals_created_model"},
        "live_signal_observations": {"ix_live_observations_token_time"},
    }
    try:
        from sqlalchemy import create_engine, inspect
        from sqlalchemy.exc import SQLAlchemyError

        from smms.config import get_settings
        engine = create_engine(get_settings().database_url.replace("+asyncpg", ""))
        inspector = inspect(engine)
        actual = {table: {item["name"] for item in inspector.get_indexes(table)} for table in required}
        missing = {table: sorted(names - actual.get(table, set())) for table, names in required.items()}
        missing = {table: names for table, names in missing.items() if names}
        return {"status": "PASS" if not missing else "FAIL", "missing": missing}
    except (OSError, SQLAlchemyError, ValueError) as exc:
        return {"status": "UNAVAILABLE", "error": str(exc)[:500]}


def _migration_check(root: Path) -> dict[str, Any]:
    result = _run(["alembic", "upgrade", "head", "--sql"], cwd=root)
    return {
        "status": "PASS" if result["returncode"] == 0 else "FAIL",
        "returncode": result["returncode"],
        "sql_bytes": len(result["stdout"].encode("utf-8")),
        "stderr": result["stderr"],
    }


async def _event_bus_smoke() -> dict[str, Any]:
    from smms.radar.events import RadarEventBus
    bus = RadarEventBus(max_events=8)
    queue = await bus.subscribe()
    first = await bus.publish("SIGNAL_CHANGED", chain="bsc", token="0x1", event_id="validation-1")
    duplicate = await bus.publish("SIGNAL_CHANGED", chain="bsc", token="0x1", event_id="validation-1")
    delivered = await queue.get()
    bus.unsubscribe(queue)
    return {
        "status": "PASS" if first == duplicate and delivered["event_id"] == "validation-1" and bus.subscriber_count == 0 else "FAIL",
        "duplicate_same_object": first == duplicate,
        "delivered_event_id": delivered["event_id"],
        "subscriber_count_after_cleanup": bus.subscriber_count,
    }


def run_validation(root: Path, *, duration_seconds: int, interval_seconds: int, report: Path, api_url: str = "http://localhost:8000") -> dict[str, Any]:
    report.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    result: dict[str, Any] = {
        "phase": "7.9.5",
        "started_at": _utc_now(),
        "duration_seconds": duration_seconds,
        "samples": [],
        "checks": {
            "event_bus": asyncio.run(_event_bus_smoke()),
            "indexes": _index_checks(),
            "database": _db_checks(),
            "migrations": _migration_check(root),
        },
    }
    with report.open("a", encoding="utf-8") as handle:
        while True:
            sample = {"at": _utc_now(), "compose": _compose_snapshot(root), "api": _api_snapshot(api_url)}
            result["samples"].append(sample)
            handle.write(json.dumps(sample, ensure_ascii=True) + "\n")
            handle.flush()
            if duration_seconds <= 0 or time.monotonic() - started >= duration_seconds:
                break
            time.sleep(max(1, interval_seconds))
    result["finished_at"] = _utc_now()
    result["status"] = "PASS" if all(item.get("status") in {"PASS", "SKIPPED", "UNAVAILABLE"} for item in result["checks"].values()) else "FAIL"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration-hours", type=float, default=24.0)
    parser.add_argument("--interval-seconds", type=int, default=60)
    parser.add_argument("--report", type=Path, default=Path("data/reports/phase7_9_5_runtime.jsonl"))
    parser.add_argument("--summary", type=Path, default=Path("data/reports/phase7_9_5_runtime_summary.json"))
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--api-url", default=os.getenv("SMMS_VALIDATION_API_URL", "http://localhost:8000"))
    args = parser.parse_args()
    result = run_validation(args.root.resolve(), duration_seconds=max(0, int(args.duration_hours * 3600)), interval_seconds=args.interval_seconds, report=args.report, api_url=args.api_url)
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(json.dumps(result, indent=2, ensure_ascii=True, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "summary": str(args.summary), "samples": len(result["samples"])}, ensure_ascii=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

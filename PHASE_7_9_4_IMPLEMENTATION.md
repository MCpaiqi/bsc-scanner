# Phase 7.9.4 Implementation

## Before

Phase 7.8-7.9.3 的模块大多可以独立调用，但 production runtime 没有闭环：Radar 仍有 DexScreener legacy surface，audit worker 对每个 token 全链 replay，external evidence 没有进入 audit/risk，live signal 没有统一 materialization，GET performance 会触发 outcome evaluation，React 不是默认 production root。

## After

Canonical BSC chain events create candidates. A typed `RadarFeatureSnapshot` is shared by Radar, the priority queue, API and Signal Engine. `AuditWorker` scopes intelligence to one token and affected wallets, calls `ExternalEvidenceOrchestrator`, resolves chain-first observations, persists provenance-aware risk, materializes a durable Signal, refreshes candidate state, emits before/after domain diffs, evaluates alert rules and creates Forward T0 only on a signal/state transition.

Compose now owns `postgres`, `redis`, `db-migrate`, `api`, `bsc-ingestion`, `radar-worker`, `forward-worker` and `web`. The React build is served at `/`; `/legacy` is explicitly deprecated. Redis fanout carries `event_id` and `origin_instance`, and the subscriber never republishes Redis-originated events.

Forward evaluation is worker-owned and idempotent. GET endpoints are read-only. Baseline samples are deterministic and independent of signal class. Existing BSC-only observations are safely backfilled with `chain='bsc'` by migration `20260912_0019`; no unknown chain is guessed.

Release artifacts are created with `scripts/build_release.py` in the project-external
`../artifacts/smms-release/` directory (and a sibling `.zip` archive). The builder
never copies the source tree into its own output: it prunes `release/`, `.git/`,
`.venv/`, `venv/`, `node_modules/`, `__pycache__/`, `.pytest_cache/`, `.ruff_cache/`,
`.mypy_cache/`, `build/`, `dist/`, `coverage/`, `tmp/`, `cache/`, `temporary/`,
 tests, local databases, `.env`, and temporary Phase/pytest/release-test directories. An explicit
in-tree output is still guarded and skipped during traversal.

Safe release procedure:

```bash
python scripts/build_release.py
python scripts/release_audit.py ../artifacts/smms-release
```

Repeat builds are idempotent and must not produce `release/release` (or deeper
recursive variants); the audit must also report no `.env`, `.git`, dependencies,
cache, or temporary files.

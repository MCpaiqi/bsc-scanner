# Smart Money Meme Scanner

Smart Money Meme Scanner (SMMS) is a Solana research platform for identifying early smart-wallet activity, generating explainable signals, and validating them with backtests and paper trading.

Phase 0 intentionally ships with paper trading as the default. No live execution is implemented. The `ENABLE_LIVE_TRADING` and `LIVE_CONFIRMATION` flags are both required before a future live adapter may be enabled.

## Quick start

```bash
python -m venv .venv
.venv\\Scripts\\Activate.ps1
pip install -e ".[dev]"
Copy-Item .env.example .env
docker compose up -d postgres redis
python scripts/init_db.py
pytest
```

Run the API with:

```bash
uvicorn smms.api.app:app --reload
```

The API exposes `GET /health`. External provider credentials are optional in Phase 0; unset providers are reported as `not_configured` rather than fabricated.

## Safety

- Runtime mode defaults to `PAPER`.
- Live execution is a guarded placeholder and raises when either safety flag is disabled.
- Secrets belong in `.env` or an external secret manager; `.env` is ignored by Git.
- UTC is used for all persisted timestamps.

See `docs/architecture.md` and `docs/deployment.md` for the Phase 0 boundaries.

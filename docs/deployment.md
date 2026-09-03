# Deployment

Copy `.env.example` to `.env`, review the values, and start PostgreSQL and Redis with `docker compose up -d postgres redis`. The app container can then be started with `docker compose up app`.

Never commit `.env` or place private keys in source code or logs. Live trading is disabled in Phase 0.


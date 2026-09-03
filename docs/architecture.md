# Architecture

Phase 0 establishes configuration, logging, dependency boundaries, and health reporting. Data ingestion, normalization, wallet intelligence, scoring, backtesting, and paper execution are intentionally deferred to later phases.

External services must be accessed through provider adapters. Missing credentials produce an explicit `not_configured` health state; the system does not invent data.


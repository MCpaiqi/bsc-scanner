# Architecture

Phase 0 establishes configuration, logging, dependency boundaries, and health reporting. Phase 1 adds the relational schema and repositories. Phase 2 adds a provider seam and a normalization layer: providers return authoritative raw payloads, while normalization produces canonical records without mutating source data.

External services must be accessed through provider adapters. Missing credentials produce an explicit `not_configured` health state; the system does not invent data.

The Solana adapter uses documented JSON-RPC methods only. DEX-specific swap decoding is an enrichment concern and is not inferred from an arbitrary token transfer.

# Architecture

Phase 0 establishes configuration, logging, dependency boundaries, and health
reporting. Phase 1 adds the relational schema and repositories. Phase 2 adds a
provider seam and a normalization layer: providers return authoritative raw
payloads, while normalization produces canonical records without mutating
source data. Phase 4 adds chain-neutral chain/launchpad seams, a read-only BSC
provider, and a verification-first Flap adapter. Phase 6 adds a bounded BSC
RPC surface, verified event metadata, idempotent event repositories, and a
single-token historical Flap orchestration seam.

External services must be accessed through provider adapters. Missing
credentials produce an explicit `not_configured` health state; the system does
not invent data.

The Solana adapter uses documented JSON-RPC methods only. The current primary
target is Flap on BNB Smart Chain; Solana remains a parallel provider.
Flap-specific event selectors and curve readers live under
`launchpads/flap`. Verified selectors may be recorded in configuration while
the launchpad remains disabled until emitter addresses and real fixtures are
complete. DEX-specific swap decoding is an enrichment concern and is not
inferred from an arbitrary token transfer.

The historical path is intentionally layered:

```text
BSC JSON-RPC
  -> Flap adapter / decoder
  -> normalized Token, TradeEvent, TokenLifecycleEvent
  -> raw-chain evidence + idempotent repositories
  -> PostgreSQL and point-in-time wallet/token calculations
```

Realtime WebSocket ingestion and live execution remain out of scope.

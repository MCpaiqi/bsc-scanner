# Phase 6 Implementation Status

**Status:** Partial implementation; historical production ingestion is gated
on missing on-chain evidence.

**Baseline:** `4f5e5d8`

## Implemented in this increment

* Added `src/smms/launchpads/flap/verification/` with strict verified address
  records, event topic declarations, Keccak topic calculation, curve facts,
  evidence levels, and a machine-readable verification report.
* Recorded the verified Portal address and event selectors in the YAML files
  while keeping `launchpads.flap.enabled: false`.
* Extended the read-only BSC provider with configurable endpoint failover,
  bounded retry/backoff, `eth_call`, `eth_chainId` validation, log-range
  chunking/deduplication, and block timestamp caching.
* Corrected the Flap trade decoder's price/market-cap naming and refused to
  normalize optional fixed-point values when their decimals are unknown.
* Added `event_index` to lifecycle events and Alembic revision
  `20260904_0006` so lifecycle identity follows the EVM receipt log index.
* Added synchronous and asynchronous idempotent repositories for TradeEvent,
  TokenLifecycleEvent, and WalletTokenPerformance.
* Added `FlapHistoricalIngestionService`, raw receipt/log/block persistence,
  weighted-average-cost Wallet x Token calculations, and a data-quality report.
* Added bounded CLI surfaces: `smms discover flap` and
  `smms backfill flap --token ...`. Both reject an omitted block range instead
  of scanning all of BSC implicitly.
* Added an explicit `verification.status` configuration gate. Enabled
  ingestion requires `VERIFIED`; the checked-in configuration remains
  `VERIFY_ON_CHAIN` and disabled until the missing emitter evidence is
  captured.
* Missing trade amounts now leave Wallet x Token financial derivations
  unavailable rather than treating unknown values as zero. Quality reports
  include the creator and render an unmeasured unknown-event count as
  `UNKNOWN`; a BUY-only sample fails the complete-trade gate.
* Migration `20260904_0006` refuses to assign synthetic log indexes to legacy
  lifecycle rows. Existing rows require an explicit manual identity backfill
  before the new `(chain, tx_hash, event_index)` constraint is applied.

## Layered runtime path

```text
BSCProvider
  -> FlapHistoricalIngestionService
  -> FlapAdapter / verified selectors
  -> Token, TradeEvent, TokenLifecycleEvent
  -> RawChainRecord + idempotent repositories
  -> Wallet and WalletTokenPerformance
```

The service accepts an optional `AsyncSession`; without one it returns the
normalized result and quality report for inspection. With a session it commits
raw evidence and canonical rows in one backfill operation.

## Verification gate still open

The implementation does not claim `INGESTION_SUCCESS` for the documented real
token yet. The following remain required:

* prove the creation/trade emitter address before filling `factory_address` or
  `trade_contract_address`;
* capture and commit a raw real TokenCreated receipt/log fixture;
* capture and commit at least one real TokenBought fixture;
* capture and commit at least one real TokenSold fixture;
* verify the Portal ABI and per-token curve state method/parameters;
* run the backfill against PostgreSQL and record its data-quality report.

The current repository tests use deterministic synthetic logs for behavior
coverage. They are not labeled as real chain evidence. No synthetic fixture is
used to satisfy the SELL requirement.

## Safety and scope

`ENABLE_LIVE_TRADING=false` remains the default and no signer, private-key
handling, swap submission, or realtime WebSocket ingestion was added. Smart
Money scoring is not part of this increment.

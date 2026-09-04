# Phase 6 Repository Audit

**Audit date:** 2026-09-04 (UTC)

**Baseline:** `4f5e5d8 feat: add configuration-driven flap launchpad adapter`

**Audit scope:** Repository inspection required by Phase 6 before adding
historical ingestion or database orchestration. This document records what the
repository can do today and what still needs an authoritative Flap source. No
Flap configuration is activated by this audit.

> This is the pre-implementation audit snapshot. The changes made after this
> audit, their current safety gates, and the remaining verification blockers are
> recorded in [PHASE_6_IMPLEMENTATION.md](PHASE_6_IMPLEMENTATION.md).

## Executive result

Phase 5 provides a conservative, configuration-driven adapter seam. It can
normalize provider records, decode explicitly configured logs, and expose an
injected bonding-curve reader. It cannot yet ingest Flap history end to end.

The repository now has enough domain columns to store a basic Token,
TradeEvent, Wallet, and Wallet x Token result, but it does not have the service
that connects those records, and it does not persist Flap raw logs through the
existing raw-record repository. The checked-in Flap YAML remains disabled; it
contains the verified Portal/topic selectors but leaves the creation/trade
emitter roles unset. The only contract address established in the Phase 6
evidence set is the official BNB mainnet Portal address; Factory, Router, and
token-specific curve roles remain blocked pending direct verification.

## 1. Phase 5 completed modules

| Module | Current responsibility | Phase 6 assessment |
| --- | --- | --- |
| `src/smms/launchpads/flap/config.py` | Immutable `FlapConfig`; address, topic, field-index, and decimal selectors are optional | Reusable; must be populated only from verified evidence |
| `src/smms/launchpads/flap/discovery.py` | One bounded `get_logs` call for configured creation events; block lookup for timestamps | Decoder seam is present; no chunking, retry, deduplication, or persistence |
| `src/smms/launchpads/flap/lifecycle.py` | Creation and configured lifecycle topic decoding | No verified lifecycle configuration; lifecycle identity has no log index |
| `src/smms/launchpads/flap/trade_decoder.py` | BUY/SELL decoding from configured receipt logs, Decimal scaling, raw log metadata | Unit-tested with synthetic logs; price/market-cap mapping needs correction before production |
| `src/smms/launchpads/flap/bonding_curve.py` | Injected state and quote reader seam with block-aware return shape | No real Portal `eth_call`, ABI, curve address, or formula |
| `src/smms/launchpads/flap/adapter.py` | Composes discovery, lifecycle, trade, timestamp enrichment, and curve seams | No historical orchestration or repository calls |
| `src/smms/launchpads/flap/_decoding.py` | Conservative ABI-word/topic/address/Decimal helpers | Useful building block; does not replace a verified ABI |

Existing Phase 5 tests cover configured discovery, synthetic BUY and SELL
decoding, timestamp enrichment, lifecycle mapping, and the injected curve
reader. They do not prove a real Flap transaction.

## 2. Current Flap data entry

The current path is:

```text
ChainProvider
  -> FlapTokenDiscovery.get_logs(from_block, to_block)
  -> LaunchpadTokenDiscovery

ChainTransaction + TransactionReceipt
  -> FlapLifecycleDecoder
  -> Token / TokenLifecycleEvent

ChainTransaction + TransactionReceipt
  -> FlapTradeDecoder
  -> TradeEvent

caller-supplied state_reader / quote_reader
  -> FlapBondingCurveAdapter
  -> BondingCurveState / Decimal quote
```

The discovery path requires an explicit block range and one RPC log request.
The adapter resolves a missing timestamp through `get_block`, but it does not
cache block timestamps. Live discovery delegates to a WebSocket-like
`subscribe_logs` method even though the BSC WebSocket implementation is an
explicit placeholder and live streaming is out of scope for this phase.

The trade decoder uses `event_index=log.log_index` and includes the original log
under `metadata_json`, which is the correct evidence direction. It is not yet
connected to `RawChainRecord`, and it currently assigns the configured
`trade_price_data_index` through a variable named `market_cap` before writing
`price`; this makes the price/market-cap contract ambiguous and must be fixed
before any historical result is trusted.

## 3. BSC provider capabilities

`src/smms/chains/bsc/rpc.py` is a read-only JSON-RPC adapter. It currently
supports:

* `eth_blockNumber`
* `eth_getBlockByNumber`
* `eth_getTransactionByHash`
* `eth_getTransactionReceipt`
* `eth_getLogs`

It normalizes blocks, transactions, receipts, and logs into the chain-neutral
models while retaining the provider payload in `raw`. Transaction value keeps a
`value_wei` raw integer in metadata, but the normalized value is immediately
scaled with 18 decimals. There is no `eth_call`, `eth_chainId` validation,
block timestamp cache, bounded retry/backoff, endpoint failover, or block-range
chunking. A single `get_logs` call can therefore hit public-RPC limits.

The configured `BSC_RPC_URL` is optional and the provider fails explicitly when
it is absent. `BSC_WS_URL` only constructs a placeholder subscription; it does
not provide a reconnecting stream.

## 4. Existing database and repositories

The Alembic history is `20260904_0001` through `20260904_0005`. The SQLAlchemy
models currently expose these relevant tables:

| Table | Useful Flap fields | Existing identity / limitation |
| --- | --- | --- |
| `tokens` | chain, address, launchpad, creator, first-seen/trade/graduation timestamps, status, metadata | Unique `(chain, address)`; no creation tx/block columns outside metadata |
| `wallets` | chain, address, activity aggregates, metadata | Unique `(chain, address)` |
| `trade_events` | chain, launchpad, token, trader, tx, block, timestamp, side, amounts, price, market cap, liquidity, event index, source, metadata | Unique `(chain, tx_hash, event_index)`; suitable for EVM log identity |
| `token_lifecycle_events` | chain, launchpad, token, event type, timestamp, tx, block, payload | Unique key omits `event_index`; not sufficient for two same-type logs in one transaction |
| `wallet_token_performance` | chain, launchpad, wallet/token pair, entry and PnL fields | Unique `(chain, wallet_address, token_address)`; no computation service |
| `token_wallet_events` | token/wallet event, amount, price, market cap, liquidity, tx signature | No chain/launchpad or event identity; do not use as the canonical Flap log table without an explicit mapping |
| `raw_chain_records` | provider, record type, external id, checksum, JSON payload | Unique `(provider, record_type, external_id)`; reusable raw evidence store |
| `ingestion_runs` | provider, run status, counters, error message | Observability row only; no Flap orchestration |

Synchronous repositories exist for Token, Wallet, and WalletTransaction.
Async repositories exist for Token, Wallet, WalletTransaction, and
RawChainRecord. There is no TradeEvent repository, lifecycle repository,
wallet-token-performance repository, or Flap ingestion service. No current
repository inserts a Flap record or links a canonical trade to a Wallet row.

## 5. Fields that can already be persisted

Once a verified log has been decoded, the current models can represent:

* Token identity: `chain`, `address`, `launchpad`, `creator_address`,
  `first_seen_at`, `status`, and `metadata_json`.
* Trade identity and facts: `chain`, `launchpad`, `token_address`,
  `trader_address`, `tx_hash`, `block_number`, UTC `timestamp`, `side`,
  Decimal `token_amount`, `native_amount`, `price`, `market_cap`,
  `liquidity`, `event_index`, `source`, and raw-log metadata.
* Wallet identity: `chain`, `address`, first/last activity, and aggregate
  fields. A service must upsert one wallet for every verified trader.
* Wallet x Token output: the existing performance table can hold derived
  position and PnL fields after a point-in-time calculation; it cannot be
  populated directly from one trade without a calculation service.
* Raw evidence: `RawChainRecord` can store a receipt/log/block payload keyed by
  a stable external id such as `tx_hash:event_index`.

These are storage capabilities, not claims that the values are currently
available from Flap or already written to PostgreSQL.

## 6. Fields and facts still missing authoritative sources

The following must remain unavailable or explicitly marked unknown until they
are verified from official documentation, a verified contract, an RPC call, or
a real receipt/log:

* Factory, Router, bonding-curve factory/implementation, and token-specific
  curve addresses.
* Full JSON ABI and the emitting contract role for each event.
* Indexed versus data parameters for every lifecycle event.
* Graduation, DEX migration, liquidity, tax/fee, and threshold semantics.
* Per-token curve state and parameters (`r`, `h`, `k`) at a historical block.
* A committed real SELL transaction/log fixture. The event signature is known,
  but a verified sample SELL must not be fabricated.
* Historical wallet positions, realized/unrealized PnL, and Wallet x Token
  performance rows.

Official curve documentation establishes general facts (18 token decimals,
maximum supply of 1,000,000,000, and constant-product terminology), but it does
not authorize hard-coding a global curve parameter set for every token.

## 7. Fake data and placeholders that must not pass the data gate

* `configs/default.yaml` and `configs/sources.yaml` keep Flap disabled. They
  contain verified Portal/event selectors but leave Factory, Router, and curve
  addresses unset with `VERIFY_ON_CHAIN` comments.
* Unit tests use synthetic addresses, topics, and ABI words. They are unit
  fixtures, not historical evidence.
* The curve adapter accepts only an injected reader and rejects an absent or
  unverified reader.
* `src/smms/main.py` only reserves top-level commands and has no `discover flap`
  or `backfill flap` command.
* `BSCWebsocketSubscription` always raises that subscriptions are not
  implemented.
* `ENABLE_LIVE_TRADING` defaults to `false`; no private-key signer or live swap
  path is part of this phase.

## 8. Phase 6 minimum change plan

Keep the existing chain-neutral seams and implement the following in order:

1. Store the verified Portal/address roles, event declarations, topic hashes,
   field selectors, and evidence metadata in a dedicated verification layer.
2. Extend the BSC read-only provider with `eth_call`, bounded block-range
   chunking, bounded retry/backoff, endpoint configuration, and a block timestamp
   cache.
3. Capture a real TokenCreated, BUY, and SELL fixture before enabling a decoder;
   add topic, field-layout, Decimal, timestamp, and lifecycle tests against the
   captured receipts/logs.
4. Add a Flap historical application service. Keep RPC, decoding, normalization,
   repository, and database responsibilities separate.
5. Reuse `RawChainRecord` for receipts/logs/blocks, add idempotent TradeEvent and
   lifecycle writes, and upsert Token and Wallet rows using their chain-scoped
   identities.
6. Implement single-token backfill first, then `discover flap` and
   `backfill flap --token ...` CLI entry points, followed by a data-quality
   report and integration tests.

No global smart-money scoring, realtime WebSocket ingestion, or live trading is
needed to pass this audit. Do not add a migration until a concrete verified
fact cannot be represented by existing columns plus raw metadata.

## 9. Files that do not need modification in this audit step

The following boundaries already express the intended Phase 6 safety model and
should remain unchanged while verification is completed:

* `src/smms/normalization/`
* `src/smms/smart_money/`
* `src/smms/risk/`
* `src/smms/execution/`
* `src/smms/ingestion/solana/`
* Existing Alembic revisions and the current Solana provider
* `configs/default.yaml` and `configs/sources.yaml` until the evidence layer is
  complete

The next implementation changes should be limited to verified Flap/BSC
transport, evidence storage, ingestion orchestration, and their tests.

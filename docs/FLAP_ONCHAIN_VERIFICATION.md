# Flap On-chain Verification

**Verification date:** 2026-09-04 (UTC)

**Network:** BNB Smart Chain mainnet

**Chain ID:** `56`

This is an evidence register, not an enabled production configuration. A fact
is promoted to adapter configuration only when its source and verification
method are recorded here or in a later signed/committed fixture. Anything not
listed as verified remains `BLOCKED: VERIFY_ON_CHAIN`.

## Evidence levels

| Level | Meaning |
| --- | --- |
| `VERIFIED` | Directly observed in an on-chain log, receipt, block, contract call, or authoritative official documentation |
| `DERIVED` | Deterministically calculated only from verified values, such as a topic hash, UTC timestamp, or normalized Decimal |
| `INFERRED` | A lifecycle or role interpretation derived from multiple verified observations; must not be presented as a direct event |
| `UNKNOWN` | No reliable source is available |

Unknown values must remain unknown; they must not be replaced with zero or a
plausible default.

## Sources

| Source | What it establishes | Use in this register |
| --- | --- | --- |
| [Flap deployed contract addresses](https://docs.flap.sh/flap/developers/deployed-contract-addresses) | Official BNB deployment address list | Portal address below |
| [Index TokenCreated events](https://docs.flap.sh/flap/developers/wallet-and-terminal-and-bot-developers/index-token-created-events.md) | Token creation event declaration and indexing workflow | `TokenCreated` signature/topic and discovery requirement |
| [Trade tokens](https://docs.flap.sh/flap/developers/wallet-and-terminal-and-bot-developers/trade-tokens.md) | BUY/SELL event declarations and trade fields | `TokenBought`/`TokenSold` signatures and data layout |
| [Bonding curve in developer perspective](https://docs.flap.sh/flap/developers/wallet-and-terminal-and-bot-developers/bonding-curve-in-developers-perspective.md) | Supply, decimals, curve terminology, and Portal state-reading guidance | Curve facts and unresolved per-token state |
| BSC JSON-RPC (`eth_getLogs`, `eth_getBlockByNumber`, receipt lookup) | Live block/log evidence | Real token and observed field positions below |

## Contract roles and addresses

| Role | Address / status | Evidence and verification method |
| --- | --- | --- |
| Portal | `0xe2cE6ab80874Fa9Fa2aAE65D277Dd6B8e65C9De0` | `VERIFIED`; exact address listed by the official deployed-addresses page |
| Factory / creation emitter | `BLOCKED: VERIFY_ON_CHAIN` | No separate Factory role has been promoted from the evidence set; verify the emitting address in a real TokenCreated receipt before mapping `factory_address` |
| Router | `BLOCKED: VERIFY_ON_CHAIN` | No verified address or ABI role in the evidence set |
| Bonding-curve factory / implementation | `BLOCKED: VERIFY_ON_CHAIN` | Must be identified from a verified contract call or receipt |
| Token-specific curve | `BLOCKED: VERIFY_ON_CHAIN` | Must be read per token; never infer it from a generic address |

The verified Portal address must not silently be copied into the adapter's
`factory_address`, `router_address`, or `bonding_curve_address` fields. The
role-to-address mapping is a separate verification step.

## Event signatures and topics

Topic hashes below were calculated as `keccak256` of the canonical event
signature and checked against the official event documentation where noted.
The actual indexed/data layout is recorded only where a real log confirmed it.

### Creation

```text
TokenCreated(uint256,address,uint256,address,string,string,string)
topic0 = 0x504e7f360b2e5fe33cbaaae4c593bc55305328341bf79009e43e0e3b7f699603
```

Status: `VERIFIED` for the declaration/topic and for the presence of a real
creation log. In the observed real log, `topics` contained topic0 and the
address fields were ABI data words:

```text
creation creator data index = 1
creation token data index   = 3
```

The dynamic string fields still require an ABI-aware decoder. The creation
emitter address must be checked from the receipt before enabling discovery.

### Trades

```text
TokenBought(uint256,address,address,uint256,uint256,uint256,uint256)
topic0 = 0xa800a2038683844fac66747f771bfdfae862eb28b16bcfa387afa9fbacce8ff7

TokenSold(uint256,address,address,uint256,uint256,uint256,uint256)
topic0 = 0x03a4693e592f5e75dc7c136acb39b146d2b4966c0e509c34f362dee02b3b861a
```

Status: `VERIFIED` for the declarations/topics and for observing a real
`TokenBought` log in a bounded BSC query. The observed trade logs had only
topic0; all seven values were ABI data words in this order:

| Data index | Field |
| ---: | --- |
| 0 | timestamp |
| 1 | token |
| 2 | buyer/seller (trader) |
| 3 | token amount |
| 4 | native amount (`eth`) |
| 5 | fee |
| 6 | post-trade price |

Adapter selectors supported by this evidence are therefore:

```text
trade token data index         = 1
trade trader data index        = 2
trade token amount data index  = 3
trade native amount data index = 4
trade price data index         = 6
```

The `TokenSold` signature/topic is verified, but a real SELL transaction/log
fixture has not been captured into the repository yet:
`ON_CHAIN_FIXTURE_REQUIRED`. No synthetic SELL fixture may be labeled real.

`Transfer`, LP movements, fee distributions, creator allocations, and internal
routing logs are not trades unless the verified event semantics prove that a
user bought or sold the token.

### Candidate lifecycle and state topics

The following hashes are deterministic calculations from candidate signatures.
They are not yet verified as the canonical emitter, lifecycle meaning, or field
layout for the sample token:

| Candidate signature | Topic0 | Status |
| --- | --- | --- |
| `LaunchedToDEX(address,address,uint256,uint256)` | `0x6e4f47630b8745b8cacbd44f42a8a33e7eea7cc08ef22fc7630f4f385784ff7d` | `BLOCKED: VERIFY_ON_CHAIN` |
| `FlapTokenProgressChanged(address,uint256)` | `0x4c35e20d1e9bce377c7d9ec1572d934e46d62961f4da8af5beb8002d5906742d` | `BLOCKED: VERIFY_ON_CHAIN` |
| `FlapTokenCirculatingSupplyChanged(address,uint256)` | `0x115c78ad17c4763fb97bca94f3e59dc8cb2e59c9d3862f24a694ec401200f562` | `BLOCKED: VERIFY_ON_CHAIN` |
| `TokenCurveSetV2(address,uint256,uint256,uint256)` | `0x71a10912a55f73d3cced0d1515c2b33c396c80342522bad0e295ccbede556f37` | `BLOCKED: VERIFY_ON_CHAIN` |
| `TokenDexSupplyThreshSet(address,uint256)` | `0x6f10bbe11587431707df676556a0551025a4f66551acdddc5793389e20f0d46e` | `BLOCKED: VERIFY_ON_CHAIN` |
| `TokenQuoteSet(address,address)` | `0x3ceb902d3c555c21c3415b6aa839104b18e4825b2f8324011ff979089a507a8c` | `BLOCKED: VERIFY_ON_CHAIN` |

Until a lifecycle event is observed and decoded from a verified ABI, a state
such as `GRADUATED` or `DEX` is at most `INFERRED`, and otherwise
`UNKNOWN`.

## Real TokenCreated evidence

The following record was returned by a bounded query against
`https://bsc-rpc.publicnode.com` and cross-checked with the creation receipt
and block timestamp. It is the first real Flap token in the Phase 6 evidence
set.

| Field | Verified value |
| --- | --- |
| Token | `0x622877b3b964285a8103cc714b5a886177077777` |
| Creator | `0x52cfb705a72eca722803cea6fd4a0c7199999999` |
| Name | `The BNBFATHER` |
| Symbol | `BSCFATHER` |
| Creation block | `119877660` |
| Block timestamp | `1788506447` (`2026-09-04 07:20:47 UTC`) |
| Creation transaction | `0xc3f048ef4df0eb697554cd2cfdfdc0624b353af63eaa7b11d4d8d777e6466342` |
| Creation log index | `83` |
| Evidence level | `VERIFIED` for the observed receipt/log fields |

The raw receipt/log payload and the exact emitting address must be persisted as
an immutable fixture before this record is used to activate a production
configuration. The record proves a real creation event; it does not prove a
Factory role, a curve address, or a graduation transition.

## Real trade evidence status

A bounded query over the creation block/transaction range observed a real
`TokenBought` event and established the seven-word data layout above. The
normalized BUY fixture still needs a committed (`tx_hash`, `block_number`,
`log_index`, `raw_log`) artifact for integration tests.

The real SELL fixture is currently:

```text
ON_CHAIN_FIXTURE_REQUIRED
```

Consequently the full requirement "real Token -> BUY -> SELL -> Wallet" is not
yet an ingestion success claim. A later gate must include at least one verified
BUY and one verified SELL, with timestamps resolved from their block numbers.

## Bonding-curve verification

Official documentation currently supports these general facts:

* maximum token supply is `1_000_000_000`;
* token decimals are `18`;
* the curve is described using a constant-product form;
* parameters are named `r`, `h`, and `k`;
* state should be read through the Portal (`getTokenV5` in older guidance and
  `getTokenV8` in the latest BNB mainnet guidance);
* curve parameters must be read per token and cached, not hard-coded globally.

The following remain `BLOCKED: VERIFY_ON_CHAIN` for the sample token:

* Portal ABI and exact `eth_call` selector;
* curve contract/implementation address;
* raw reserve, supply, virtual-reserve, price, progress, and liquidity state;
* graduation threshold and migration condition;
* a verified historical curve state at a specified block.

The Phase 5 curve reader must therefore remain an injected seam. A quote or
price derived from an invented formula is not acceptable evidence.

## RPC observations and risks

| Endpoint | Observation | Required mitigation |
| --- | --- | --- |
| `https://bsc-dataseed.binance.org/` | Block and transaction lookups worked; broad `eth_getLogs` returned `limit exceeded` | Use bounded ranges and chunking; do not rely on one full-history request |
| `https://bsc-rpc.publicnode.com` | Bounded `eth_getLogs` worked; repeated requests once produced transient TLS `UNEXPECTED_EOF` | Configurable endpoints, bounded retry/backoff, and failover |

No endpoint should be treated as an authoritative data source by itself. RPC
errors, invalid JSON-RPC payloads, empty results, 429s, 5xx responses, and
connection resets must be observable and retried only a finite number of times.

## Verification gate before implementation

The adapter may be enabled only after the following are attached to this
register or to committed fixtures:

1. The Portal/creation-emitter role is proven from a real receipt.
2. Full ABI declarations and indexed/data positions are checked for creation,
   BUY, SELL, and any lifecycle event actually used.
3. The real creation record, one BUY, and one SELL each have raw log evidence.
4. Block timestamps are resolved from `eth_getBlockByNumber` and retained with
   the block reference.
5. Per-token curve state can be read at a requested block, or remains
   explicitly `UNKNOWN`.
6. The evidence is written idempotently using `(chain, tx_hash, event_index)`
   for trades and an equivalent verified identity for lifecycle events.

Until those gates pass, the checked-in configuration remains disabled and
`ENABLE_LIVE_TRADING=false` remains unchanged.

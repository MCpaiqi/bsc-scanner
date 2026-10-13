# Phase 7.8.3 - External Evidence Provider Layer

## Scope

This phase adds a secondary-evidence boundary for Solana token candidates that
already exist in SMMS. External providers enrich a candidate; they never create
Radar candidates and never write a risk snapshot directly.

The initial providers are:

- **DexScreener**: market price, liquidity, market cap, volume, pair metadata,
  website and social links.
- **GoPlus**: contract-security, honeypot/tax, owner privilege, blacklist,
  mint/proxy and trading-restriction indicators.

GMGN is intentionally represented only by the provider interface. No private,
reverse-engineered or unauthorised endpoint is used.

## Provider contract

`ExternalEvidenceProvider` exposes capability methods:

- `fetch_token_market`
- `fetch_token_security`
- `fetch_holder_information`
- `fetch_social_metadata`

Providers may return `None`/an empty observation set for capabilities they do
not implement. Every upstream field is converted to an `EvidenceObservation`
before it can be consumed by risk or Radar code.

An observation carries chain, subject identity, field/value, source and source
type, observation/fetch timestamps, confidence, evidence level, an auditable
bounded raw reference, and an expiry timestamp. Third-party evidence uses
`TRUSTED_PROVIDER` (or an equivalent non-chain-verified level); it must never be
labelled `ON_CHAIN_VERIFIED`.

## Reliability and data safety

Provider calls are protected by per-key TTL caching and request coalescing,
rate limiting, bounded exponential backoff for transient failures, and a
provider-health state. Timeouts, HTTP 429, malformed payloads and partial
responses are represented as unavailable/unknown evidence. Expired cached
observations are marked stale and are not silently presented as current.

Raw upstream payloads are not persisted by this layer. When a raw reference is
needed for audit, it is redacted and size-bounded; credentials and authorization
headers are excluded.

## Configuration

All provider URLs, enable flags, timeouts, API keys, rate limits, cache TTLs and
maximum backoff are environment-backed in `smms.config.settings.Settings`.
`.env.example` contains placeholders only. GoPlus is disabled by default.

## Verification

Unit tests use mocked HTTP transports exclusively. Coverage includes successful
partial payloads, missing fields, malformed responses, timeout, HTTP 429,
backoff/health transitions, stale evidence, `UNKNOWN` evidence, and cache
deduplication. No test accesses a real provider endpoint.

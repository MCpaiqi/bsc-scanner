# Phase 7.8.4 - Evidence Resolver

Implemented a side-effect-free resolver that turns chain, derived-chain and
external observations into auditable field-level conclusions.

## Evidence hierarchy

`ON_CHAIN_VERIFIED > DERIVED_FROM_CHAIN > TRUSTED_PROVIDER >
UNVERIFIED_PROVIDER > UNKNOWN`. Legacy `DERIVED` and `INFERRED` names are
accepted as aliases without changing their meaning.

## Resolution and freshness

`EvidenceResolver` returns `resolved_value`, `resolution_source`, evidence
level, confidence, freshness (`FRESH`, `STALE`, `EXPIRED`, `UNKNOWN`), and
conflict metadata including all conflicting provider sources. Chain evidence
always outranks provider evidence. TTL defaults are field-sensitive: prices
are short-lived, holder concentration is short, social metadata is longer,
and contract code is effectively immutable.

## Integration

Risk analysis accepts optional resolved evidence and preserves its complete
provenance in `TokenRiskSnapshot.evidence_provenance`. External wallet labels
are retained as independent evidence and do not modify smart-money scores.
Radar candidate detail exposes an `evidence` summary when token evidence is
available.

## Tests

Coverage includes chain-over-provider precedence, provider fallback, stale and
expired freshness, visible conflicts, UNKNOWN preservation, and isolation of
provider wallet labels from smart-money scoring.

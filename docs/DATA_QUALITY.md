# Flap Data Quality

**Report status:** Pending complete single-token backfill.

This report deliberately uses `UNKNOWN` or `PENDING` instead of zero when a
measurement has not been established. The creation evidence below is real; the
trade counts are not yet a committed historical result.

## Sample token

| Field | Value |
| --- | --- |
| Token | `0x622877b3b964285a8103cc714b5a886177077777` |
| Creator | `0x52cfb705a72eca722803cea6fd4a0c7199999999` |
| Creation block | `119877660` |
| Creation transaction | `0xc3f048ef4df0eb697554cd2cfdfdc0624b353af63eaa7b11d4d8d777e6466342` |
| Creation log index | `83` |
| Creation timestamp | `2026-09-04 07:20:47 UTC` |
| Evidence level | `VERIFIED` for the creation receipt/log |

## Backfill quality gate

| Measure | Current status |
| --- | --- |
| Block range | `PENDING: record exact bounded trade range` |
| BUY count | `PENDING: commit real TokenBought fixture` |
| SELL count | `PENDING: ON_CHAIN_FIXTURE_REQUIRED` |
| Wallet count | `UNKNOWN` |
| Decode errors | `UNKNOWN` until the real fixture run |
| Timestamp errors | `UNKNOWN` until the real fixture run |
| Duplicate events | `UNKNOWN` until the real fixture run |
| Unknown events | `UNKNOWN` until the real fixture run |
| Data completeness | `CREATION_VERIFIED_ONLY` |
| Overall | `NOT_INGESTION_SUCCESS` |

## Required final report

After a real PostgreSQL backfill, replace the pending values with the output of
`FlapDataQualityReport.render()` and retain the raw evidence identities for
every row:

```text
(chain, block_number, tx_hash, event_index, source)
```

Graduation and migration may remain `UNKNOWN` when no verified lifecycle event
or state transition exists; they must not be inferred from a missing row.

The rendered report always includes `Creator`. `Unknown Events: UNKNOWN` means
the scan did not measure an unknown-event count; an explicit numeric zero is
reserved for a scan that actually measured no unknown events. A token is not a
successful historical backfill until both BUY and SELL events are decoded with
complete block, timestamp, and evidence identities.

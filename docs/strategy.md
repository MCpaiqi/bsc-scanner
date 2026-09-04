# Strategy

Phase 3 establishes the historical research inputs. Winner discovery is evaluated at an explicit `as_of` timestamp and requires `ath_multiple >= min_multiple` within the configured lookback. Future tokens, stale samples, missing multiples, and non-winner outcomes are excluded rather than estimated.

Early buyers are derived from normalized transactions. A candidate must have `side=BUY`, the target token as `token_out`, and a timestamp between token creation and `max_entry_hours`. Transfer, LP, airdrop, internal-routing, and unknown event types are excluded. Wallets are de-duplicated by address and ranked by first buy time, buy size, then address; Bot metadata remains attached for later classification.

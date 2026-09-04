# Architecture

SMMS is a multi-chain, multi-launchpad Smart Money Meme Intelligence Engine.

```text
ChainProvider
    -> Launchpad adapter
        -> Token / TradeEvent / TokenLifecycleEvent
            -> research, scoring, signals, backtests, paper execution
```

The BSC provider owns JSON-RPC transport and normalized blocks, transactions,
receipts, and logs. The Flap adapter owns Flap-specific selectors and decoding;
those details do not leak into core normalization or smart-money modules.

Current primary target: Flap on BNB Smart Chain.

Existing support: the Phase 2 Solana provider remains available as a parallel
data source.

Safety boundary: live trading is not implemented for this phase. Unknown Flap
chain facts remain configuration gaps marked `VERIFY_ON_CHAIN` rather than
guessed values.

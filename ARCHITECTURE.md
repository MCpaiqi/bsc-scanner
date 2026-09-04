# Architecture

SMMS is a multi-chain, multi-launchpad Smart Money Meme Intelligence Engine.

```text
ChainProvider
    -> Launchpad adapter
        -> Token / TradeEvent / TokenLifecycleEvent
            -> research, scoring, signals, backtests, paper execution
```

The BSC provider owns bounded JSON-RPC transport, retry/failover, block
timestamps, and normalized blocks, transactions, receipts, and logs. The Flap
adapter owns verified selectors and decoding; those details do not leak into
core normalization or smart-money modules. Historical orchestration persists
raw evidence before writing canonical event rows.

Current primary target: Flap on BNB Smart Chain.

Existing support: the Phase 2 Solana provider remains available as a parallel
data source.

Safety boundary: live trading is not implemented for this phase. Unknown Flap
chain facts remain configuration gaps marked `VERIFY_ON_CHAIN` rather than
guessed values. The checked-in Flap configuration remains disabled until the
creation/trade emitter roles and real BUY/SELL fixtures pass verification.

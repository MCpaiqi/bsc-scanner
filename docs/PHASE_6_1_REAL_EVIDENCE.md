# Phase 6.1 Real Evidence

状态：**BLOCKED**（2026-09-05 UTC）。

本文件只接受能用 `chain_id + contract + tx_hash + block_number + log_index` 从 BSC RPC 重新定位的证据。仓库中的 fixture 是行为测试数据，不能填入下表。

| 项目 | 证据来源 | 状态 |
| --- | --- | --- |
| Flap Portal | 官方部署地址页；需 RPC `eth_getCode` 复核 | VERIFIED（地址）；bytecode/角色待复核 |
| TokenCreated | 官方事件声明；缺少本地最小 manifest | UNKNOWN |
| BUY | 需要真实 receipt/log | UNKNOWN |
| SELL | 需要真实 receipt/log | UNKNOWN |
| Wallet / Position | 由真实 BUY/SELL DERIVED | UNKNOWN |
| PnL / ROI | 由 Position DERIVED | UNKNOWN |

## Required manifest shape

真实样本应放入 `data/evidence/flap/manifest.json`，每条记录至少包含：

```json
{
  "chain": "bsc",
  "chain_id": 56,
  "contract_address": "0x...",
  "event_name": "TokenSold",
  "tx_hash": "0x...",
  "block_number": 0,
  "block_timestamp": "2026-...Z",
  "log_index": 0,
  "topic0": "0x...",
  "topics": [],
  "data": "0x...",
  "decoded_fields": {},
  "evidence_level": "VERIFIED"
}
```

使用 `smms verify flap --tx <TX_HASH>` 可重新读取 receipt、校验 topic/emitter、恢复 block timestamp 并输出同样的最小证据结构。

在取得真实 TokenCreated、BUY、SELL 和 PostgreSQL 回填前，Position/PnL 只能标记 UNKNOWN，Phase 6.1 不得标记 COMPLETE。

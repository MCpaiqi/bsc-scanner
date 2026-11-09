# 数据模型

Phase 1 保存后续数据摄取、评分、信号、回测和模拟交易所需的研究数据。Phase 4 将模型扩展到多链和多发射平台，加入规范的 `TradeEvent`、`TokenLifecycleEvent`、钱包表现快照和聪明钱评分历史。所有时间戳均为带时区的 UTC；金额使用 `NUMERIC`，禁止使用浮点数；链上金额同时保留可兼容原始整数和标准化值的字段。

| 数据表 | 职责 | 幂等键 / 主查询键 |
| --- | --- | --- |
| `tokens` | 链/发射平台维度的 Token 档案、生命周期和市场快照 | `(chain, address)` |
| `wallets` | 钱包档案和聚合指标 | `(chain, address)` |
| `wallet_transactions` | 标准化钱包交易 / swap | `(wallet_id, signature)` |
| `token_wallet_events` | Token 维度的买入、卖出、转账和 LP 事件 | 按 Token、钱包和时间建立索引 |
| `wallet_scores` | 不可变的钱包评分历史 | `(wallet_id, timestamp, version)` |
| `token_scores` | 不可变的 Token 评分历史 | `(token_id, timestamp, version)` |
| `signals` | 可解释研究信号 | 按 Token 和创建时间建立索引 |
| `paper_trades` | 未来的模拟交易账本 | 关联 Token 和可选信号 |
| `raw_chain_records` | 保存来源原始数据 | `(provider, record_type, external_id)` |
| `trade_events` | 跨链/发射平台统一的 BUY、SELL 事件 | `(chain, tx_hash, event_index)` |
| `token_lifecycle_events` | 规范 Token 创建和生命周期变化 | `(chain, tx_hash, event_index)` |
| `wallet_token_performance` | 时间点一致的钱包/Token 表现 | `(chain, wallet_address, token_address)` |
| `wallet_performance` | 时间点一致的跨 Token 钱包聚合 | `(chain, wallet_address, as_of)` |
| `smart_money_scores` | 可解释、带版本的钱包评分 | `(chain, wallet_address, as_of, version)` |
| `ingestion_runs` | 数据摄取可观测性 | 运行标识 |

`raw_chain_records` 独立于标准化交易/事件记录保存来源数据。后续标准化结果必须在元数据中引用来源标识，且不得覆盖原始 payload。EVM 事件身份由交易哈希和回执日志索引共同确定；同一交易可以包含多个交易事件或生命周期事件。

## 数据库迁移

运行 `python scripts/init_db.py` 应用当前 Alembic head（目前为 `20260904_0006`）。生产环境的结构变更必须新增 Alembic revision，禁止直接对生产数据库调用 `Base.metadata.create_all`。

`20260904_0006` 不会为历史生命周期记录伪造 `event_index=0`。在线迁移发现旧数据时会拒绝继续，必须先依据原始回执人工回填身份。离线 `--sql` 模式只生成 SQL，不执行该前置数据检查。


分析工作流独立于规范链上研究事实。`radar_annotations` 以 `(chain, token_address)` 标识共享备注，`revision` 对条件写入递增；删除清除人工内容并保留版本标记，防止旧正数版本编辑器覆盖重建后的备注。`alert_rules.token_address` 为空表示GLOBAL，非空表示TOKEN；TOKEN目标必须属于BSC。可选唯一 `request_id` 对创建重试去重，逻辑删除保留目标、请求身份与 `alert_history`。这些人工工作流字段不作为已验证链上事实输入。

# Flap 数据质量

**报告状态：** 等待完成单 Token 历史回填。

当某项测量尚未建立时，本报告使用 `UNKNOWN` 或 `PENDING`，绝不以零或看似合理的默认值代替。下面的创建证据是真实记录；交易数量尚未形成已提交的历史结果。

## 示例 Token

| 字段 | 值 |
| --- | --- |
| Token | `0x622877b3b964285a8103cc714b5a886177077777` |
| 创建者 | `0x52cfb705a72eca722803cea6fd4a0c7199999999` |
| 创建区块 | `119877660` |
| 创建交易 | `0xc3f048ef4df0eb697554cd2cfdfdc0624b353af63eaa7b11d4d8d777e6466342` |
| 创建日志索引 | `83` |
| 创建时间 | `2026-09-04 07:20:47 UTC` |
| 证据等级 | 创建回执/日志为 `VERIFIED` |

## 回填质量门禁

| 指标 | 当前状态 |
| --- | --- |
| 区块范围 | `PENDING: record exact bounded trade range` |
| BUY 数量 | `PENDING: commit real TokenBought fixture` |
| SELL 数量 | `PENDING: ON_CHAIN_FIXTURE_REQUIRED` |
| 钱包数量 | `UNKNOWN` |
| 解码错误 | `UNKNOWN`，等待真实 fixture 执行 |
| 时间戳错误 | `UNKNOWN`，等待真实 fixture 执行 |
| 重复事件 | `UNKNOWN`，等待真实 fixture 执行 |
| 未知事件 | `UNKNOWN`，等待真实 fixture 执行 |
| 数据完整度 | `CREATION_VERIFIED_ONLY` |
| 总体结果 | `NOT_INGESTION_SUCCESS` |

完成真实 PostgreSQL 回填后，应使用 `FlapDataQualityReport.render()` 的输出替换待定值，并为每一行保留 `(chain, block_number, tx_hash, event_index, source)` 证据身份。

毕业和迁移在没有验证事件或状态变化时可以保持 `UNKNOWN`，不得从缺失记录推断。只有 BUY、SELL、区块、时间戳和证据身份均完整时，Token 才能通过历史回填门禁。

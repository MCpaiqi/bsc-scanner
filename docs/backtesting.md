# 回测

历史数据集由数据库中的 BSC 事实构建。特征价格只包含信号时点及以前的
记录；未来价格单独进入评价路径。未来观察值不得参与信号生成。

自 `dataset-v3` 起，重建会排除 Pair/TradeEvent 的非 canonical、removed 记录，
以及市场和生命周期记录上的 removed、orphaned、orphaned confirmation 标记。
已撤销的 CREATED 证据不能通过 Token 上缓存的 launchpad/first_seen_at 字段
重新进入研究 universe；没有创建事件的旧数据仍保留原有发现时间回退。

风险使用 `risk.overall_risk`。导出继续保留 `risk.level`，回测兼容此前保存的
level-only 数据集；缺证据仍返回 UNKNOWN，CRITICAL 保持 HIGH_RISK。

旧的物化数据集不会自动改写。修正过滤规则后，应使用新的 dataset-version
构建相同研究区间，再比较 config_hash、覆盖报告和结果；旧派生快照也需要
按其来源重新计算。示例：

```powershell
smms dataset build --chain bsc --from 2026-09-14T11:22:48+00:00 --to 2026-09-14T12:37:49+00:00 --dataset-version bsc-canonical-v3-20260914
smms backtest --dataset bsc-canonical-v3-20260914 --strategy composite
```

Forward 从 `forward-v2` 起使用有来源的美元价格、有限取价容差和正确的峰值
回撤；与历史回测共用 `price-path-v2` 算法，并保留旧版结果。
详见 [前瞻评价口径](FORWARD_EVALUATION.md)。

实时 Alpha 从 `experimental-v3` 起使用持久化扫描回执证明覆盖，缺证据保持
UNKNOWN，未完成任务在停机后继续重试。命中率只统计同一执行口径下完整覆盖的
1 分钟样本，见 [Alpha 覆盖与恢复](ALPHA_COVERAGE.md)。Forward 的观测价格路径
不能替代完整区块覆盖证明。旧派生数据的完整失效/重建仍属于
[验收清单](RESEARCH_RELIABILITY.md) 中未完成的 R3。

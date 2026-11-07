import type { LivePerformance as LivePerformanceData } from "../types/radar";
import "./context-panels.css";

export function LivePerformance({ data, loading = false, failed = false, readAt, onRetry }: {
  data: LivePerformanceData | null; loading?: boolean; failed?: boolean; readAt?: Date | null; onRetry?: () => void;
}) {
  const current = data?.data_status === "CURRENT";
  const row = current ? data?.horizons["5m"]?.HIGH_SIGNAL : undefined;
  const status = data?.data_status === "REBUILDING" ? `统计重建中，结果暂不可用${data.withheld_count == null ? "" : `（待重建 ${data.withheld_count} 条）`}。`
    : !current ? "统计暂不可用，状态待确认。" : row ? "统计已读取" : "暂无可用的高信号统计。";
  const medianReturn = row?.median_return;

  return <section className="context-panel performance-panel" aria-label="实时模型表现">
    <div className="context-panel-heading">
      <h2>前瞻观测</h2>
      <span className="context-window">5m</span>
    </div>
    <p className="performance-description">高信号代币 · 5 分钟表现</p>
    <p className="research-status" role={failed ? "alert" : "status"}>{failed ? data ? "更新失败，保留上次读取结果。" : "统计读取失败。" : loading ? "正在读取统计…" : status}{readAt && ` 上次读取 ${readAt.toLocaleTimeString()}`}</p>
    {failed && onRetry && <button type="button" onClick={onRetry}>重新读取统计</button>}
    <dl className="performance-metrics">
      <div>
        <dt>观测样本</dt>
        <dd>{row?.sample_count ?? "—"}<span>个</span></dd>
      </div>
      <div>
        <dt>中位收益</dt>
        <dd className={medianReturn == null ? "is-unavailable" : medianReturn > 0 ? "is-positive" : medianReturn < 0 ? "is-negative" : ""} title={medianReturn == null ? "暂无数据" : undefined}>
          {medianReturn == null ? "—" : `${medianReturn > 0 ? "+" : ""}${(medianReturn * 100).toFixed(1)}%`}
        </dd>
      </div>
    </dl>
  </section>;
}

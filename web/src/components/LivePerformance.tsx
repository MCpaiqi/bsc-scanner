import type { LivePerformance as LivePerformanceData } from "../types/radar";
import "./context-panels.css";

export function LivePerformance({ data }: { data: LivePerformanceData | null }) {
  if (!data) return null;
  const row = data.horizons["5m"]?.HIGH_SIGNAL;
  const medianReturn = row?.median_return;

  return <section className="context-panel performance-panel" aria-label="实时模型表现">
    <div className="context-panel-heading">
      <h2>前瞻观测</h2>
      <span className="context-window">5m</span>
    </div>
    <p className="performance-description">高信号代币 · 5 分钟表现</p>
    <dl className="performance-metrics">
      <div>
        <dt>观测样本</dt>
        <dd>{row?.sample_count ?? 0}<span>个</span></dd>
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

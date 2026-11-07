import type { HealthSnapshot, SourceHealthRecord } from "../types/radar";

const sourceOrder = [
  ["bsc_websocket", "BSC WebSocket"],
  ["bsc_http", "BSC HTTP"],
  ["postgresql", "PostgreSQL"],
  ["redis", "Redis"],
  ["dexscreener", "DexScreener"],
  ["goplus", "GoPlus"],
  ["audit_queue", "Audit Queue"],
  ["sse", "SSE"],
] as const;

function displayStatus(value: unknown): string {
  const status = String(value ?? "UNKNOWN").toUpperCase();
  return ["HEALTHY", "DEGRADED", "STALE", "DOWN", "UNKNOWN"].includes(status)
    ? status
    : "UNKNOWN";
}
const statusLabels: Record<string, string> = { HEALTHY: "健康", DEGRADED: "降级", STALE: "过期", DOWN: "故障", UNKNOWN: "未知" };

function sourceRecord(snapshot: HealthSnapshot | null, key: string): SourceHealthRecord {
  const source = snapshot?.sources?.[key] ?? snapshot?.source_health?.[key];
  return (source && typeof source === "object" ? source : {}) as SourceHealthRecord;
}

function number(value: unknown): string {
  return typeof value === "number" ? value.toLocaleString(undefined, { maximumFractionDigits: 2 }) : "未知";
}

export function DiagnosticsPanel({ health, radarHealth, stream }: { health: HealthSnapshot | null; radarHealth: HealthSnapshot | null; stream: HealthSnapshot | null }) {
  const snapshot = health ?? radarHealth;
  const metrics = snapshot?.metrics ?? radarHealth?.metrics ?? {};
  const chain = (metrics.chain ?? {}) as Record<string, unknown>;
  const ingestion = (metrics.ingestion ?? {}) as Record<string, unknown>;
  const audit = (metrics.audit ?? {}) as Record<string, unknown>;
  const sse = (metrics.sse ?? {}) as Record<string, unknown>;

  return (
    <main className="diagnostics-page" aria-label="健康与诊断">
      <div className="diagnostics-heading">
        <div>
          <span className="section-label">生产雷达可观测性</span>
          <h2>健康 / 诊断</h2>
        </div>
        <span className={`diagnostics-status ${displayStatus(snapshot?.observability?.status ?? snapshot?.status).toLowerCase()}`}>
          {statusLabels[displayStatus(snapshot?.observability?.status ?? snapshot?.status)]}
        </span>
      </div>
      <section className="diagnostics-sources" aria-label="数据源健康注册表">
        <div className="panel-heading"><span>数据源健康注册表</span><span className="last-sync">无敏感信息</span></div>
        <div className="diagnostics-source-grid">
          {sourceOrder.map(([key, label]) => {
            const record = sourceRecord(health, key);
            const status = displayStatus(record.status);
            return <div className="diagnostics-source" key={key}>
              <div className="diagnostics-source-title"><span className={`health-dot ${status.toLowerCase()}`} /><strong>{label}</strong><span className={`diagnostics-pill ${status.toLowerCase()}`}>{statusLabels[status]}</span></div>
              <dl>
                <dt>新鲜度</dt><dd>{statusLabels[displayStatus(record.freshness)]}</dd>
                <dt>错误数</dt><dd>{number(record.error_count)}</dd>
                <dt>延迟</dt><dd>{record.latency_ms == null ? "未知" : `${number(record.latency_ms)} 毫秒`}</dd>
              </dl>
            </div>;
          })}
        </div>
      </section>
      <section className="diagnostics-metrics" aria-label="运行指标">
        <div className="panel-heading"><span>运行指标</span><span className="last-sync">滚动窗口</span></div>
        <div className="metric-grid">
          <div><small>链头</small><strong>{number(chain.head ?? metrics.chain_head)}</strong></div>
          <div><small>已处理区块</small><strong>{number(chain.processed_block ?? metrics.processed_block)}</strong></div>
          <div><small>区块延迟</small><strong>{number(chain.block_lag ?? metrics.block_lag)}</strong></div>
          <div><small>事件摄取 / 秒</small><strong>{number(ingestion.event_rate ?? metrics.event_ingestion_rate)}</strong></div>
          <div><small>解码失败 / 秒</small><strong>{number(ingestion.decode_failure_rate ?? metrics.decode_failure_rate)}</strong></div>
          <div><small>候选发现 / 秒</small><strong>{number(metrics.candidate_discovery_rate ?? metrics.candidates?.discovery_rate)}</strong></div>
          <div><small>审计队列深度</small><strong>{number(audit.queue_depth ?? metrics.audit_queue_depth)}</strong></div>
          <div><small>审计失败率</small><strong>{number(audit.failure_rate ?? metrics.audit_failure_rate)}</strong></div>
          <div><small>SSE 客户端 / 事件</small><strong>{number(sse.client_count ?? metrics.sse_client_count)} / {number(sse.event_rate ?? metrics.sse_event_rate)}</strong></div>
        </div>
      </section>
      {stream?.freshness && String(stream.freshness).toUpperCase() !== "FRESH" && (
        <div className="diagnostics-warning">BSC 数据流当前为 {statusLabels[displayStatus(stream.freshness)]}；实时雷达数值已明确标注。</div>
      )}
    </main>
  );
}

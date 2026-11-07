import type { HealthSnapshot } from "../types/radar";
import "./context-panels.css";

const sources = [
  ["BSC WebSocket", "bsc_websocket"],
  ["HTTP 备用", "bsc_http"],
  ["PostgreSQL", "postgresql"],
  ["Redis", "redis"],
  ["DexScreener", "dexscreener"],
  ["GoPlus", "goplus"],
  ["审计队列", "audit_queue"],
  ["SSE 实时流", "sse"],
] as const;

function status(value: unknown): string {
  const text = String(value ?? "UNKNOWN").toUpperCase();
  if (["OK", "READY", "HEALTHY"].includes(text)) return "HEALTHY";
  if (text === "DEGRADED") return "DEGRADED";
  if (text === "NOT_CONFIGURED") return "NOT_CONFIGURED";
  if (text === "STALE") return "STALE";
  if (["DOWN", "ERROR"].includes(text)) return "DOWN";
  return "UNKNOWN";
}

const statusLabels: Record<string, string> = {
  HEALTHY: "正常", DEGRADED: "降级", NOT_CONFIGURED: "未配置", STALE: "已过期", DOWN: "故障", UNKNOWN: "未知",
};

export function SourceHealth({ health, radarHealth, stream }: {
  health: HealthSnapshot | null;
  radarHealth: HealthSnapshot | null;
  stream: HealthSnapshot | null;
}) {
  const merged = { ...(health ?? {}), ...(radarHealth ?? {}), ...(stream ?? {}) };
  const sourceMap = {
    ...(health?.sources ?? {}), ...(health?.source_health ?? {}),
    ...(radarHealth?.sources ?? {}), ...(radarHealth?.source_health ?? {}),
    ...(stream?.sources ?? {}), ...(stream?.source_health ?? {}),
  };
  const rows = sources.map(([name, key]) => ({ name, state: status(sourceMap[key]?.status ?? merged[key]) }));
  const healthyCount = rows.filter(({ state }) => state === "HEALTHY").length;

  return <section className="context-panel source-health-panel" aria-label="数据源健康">
    <div className="context-panel-heading">
      <h2>数据源状态</h2>
      <span className="context-panel-meta">{healthyCount} / {rows.length} 正常</span>
    </div>
    <ul className="source-status-list">
      {rows.map(({ name, state }) => <li className="source-status-row" key={name}>
        <span className="source-status-name">{name}</span>
        <span className={`source-status-value is-${state.toLowerCase()}`}>
          <i aria-hidden="true" />{statusLabels[state]}
        </span>
      </li>)}
    </ul>
  </section>;
}

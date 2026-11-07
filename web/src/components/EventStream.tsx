import type { EventSyncState, RadarEvent } from "../types/radar";
import "./context-panels.css";

const labels: Record<string, string> = {
  CANDIDATE_DISCOVERED: "新代币", CANDIDATE_UPDATED: "雷达更新", SMART_MONEY_ENTRY: "聪明钱入场",
  EARLY_BUYER_UPDATE: "早期买家", RISK_CHANGED: "风险变更", SIGNAL_CHANGED: "信号变更",
  AUDIT_STARTED: "审计开始", AUDIT_COMPLETED: "审计完成", AUDIT_FAILED: "审计失败",
  ALERT_TRIGGERED: "提醒触发", SOURCE_DEGRADED: "数据源降级",
};

export function EventStream({ events, connected, syncState, onRetry }: { events: RadarEvent[]; connected: boolean; syncState?: EventSyncState; onRetry?: () => void }) {
  return <section className="context-panel activity-panel" aria-label="实时事件流">
    <div className="context-panel-heading">
      <h2>实时动态</h2>
      <span className={`activity-connection ${connected ? "is-live" : "is-offline"}`}>
        <i aria-hidden="true" />{connected ? "已连接" : "重新连接中"}
      </span>
    </div>
    {syncState && <div className="stream-recovery" role="status" aria-live="polite">
      {syncState === "refreshing" ? "正在重新同步数据…" : syncState === "failed" ? "数据未同步，请重试" : syncState === "broker_down" ? "实时链路中断，数据可手动刷新" : syncState === "broker_unknown" ? "连接成功，链路待确认" : "快照已刷新"}
      {onRetry && (syncState === "failed" || syncState === "broker_down") && <button type="button" onClick={onRetry}>重新刷新</button>}
    </div>}
    {events.length ? <ol className="activity-list">
      {events.slice(0, 16).map((event) => {
        const token = event.token_address ?? event.token;
        const time = new Date(event.occurred_at);
        return <li className={`activity-row event-${event.event_type.toLowerCase()}`} key={event.event_id}>
          <span className="activity-marker" aria-hidden="true"><i /></span>
          <div className="activity-content">
            <div className="activity-row-heading">
              <span className="activity-kind">{labels[event.event_type] ?? event.event_type}</span>
              <time dateTime={event.occurred_at} title={time.toLocaleString()}>
                {time.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
              </time>
            </div>
            <div className="activity-identity">
              <span title={token}>{token ? `${token.slice(0, 6)}...${token.slice(-4)}` : "未知代币"}</span>
              <span className="activity-chain">{event.chain.toUpperCase()}</span>
            </div>
          </div>
        </li>;
      })}
    </ol> : <div className="activity-empty">
      <div className="activity-empty-icon" aria-hidden="true">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
          <path d="M3 12h4l3-7 4 14 3-7h4" />
        </svg>
      </div>
      <strong>等待新的链上动态</strong>
      <p>{connected ? "已连接事件流，新信号将在此出现" : "连接恢复后，实时动态将在此更新"}</p>
    </div>}
  </section>;
}

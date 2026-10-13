import type { ReactNode } from "react";
import type { HealthSnapshot } from "../types/radar";
import { WorkspaceIcon } from "./WorkspaceIcon";

export type WorkspaceView = "RADAR" | "ALPHA" | "WALLET" | "DIAGNOSTICS";
const navigation = [
  { id: "RADAR", icon: "radar", label: "市场雷达", number: "01" },
  { id: "ALPHA", icon: "alpha", label: "Alpha 研究", number: "02" },
  { id: "WALLET", icon: "wallet", label: "钱包情报", number: "03" },
  { id: "DIAGNOSTICS", icon: "health", label: "系统状态", number: "04" },
] as const;

export function WorkspaceShell({ active, onNavigate, connected, stream, loading, onRefresh, children }: {
  active: WorkspaceView;
  onNavigate: (view: WorkspaceView) => void;
  connected?: boolean;
  stream?: HealthSnapshot | null;
  loading?: boolean;
  onRefresh?: () => void;
  children: ReactNode;
}) {
  const runtime = stream?.runtime_worker_status;
  const workerStatus = String(runtime?.status ?? stream?.status ?? "UNKNOWN").toUpperCase();
  const heartbeatAge = stream?.worker_heartbeat_age_seconds;
  const workerFailed = ["DEGRADED", "FAILED", "DOWN", "ERROR", "STOPPED"].includes(workerStatus);
  const heartbeatExpired = typeof heartbeatAge === "number" && heartbeatAge >= 90;
  const workerActive = ["RUNNING", "IDLE_HEALTHY", "CATCHING_UP", "LIVE"].includes(workerStatus) && typeof heartbeatAge === "number" && heartbeatAge < 90;
  const liveReady = workerActive && runtime?.live_ready === true;
  const lag = runtime?.block_lag ?? runtime?.lag ?? stream?.block_lag;
  const lagLabel = typeof lag === "number" && lag >= 0 ? `同步落后 ${lag.toLocaleString()} 区块` : undefined;
  const chainStatus = workerFailed ? "链上采集异常" : heartbeatExpired ? "采集心跳过期" : liveReady ? "链上已同步" : workerActive ? (typeof lag === "number" && lag > 0 ? lagLabel : "链上采集中") : "链同步待确认";
  return <div className="app-shell terminal-shell">
    <aside className="app-sidebar">
      <a className="workspace-brand" href="/" aria-label="SMMS 首页">
        <span className="brand-mark"><WorkspaceIcon name="radar" size={24} /></span>
        <span className="brand-copy"><strong>SMMS<span>•</span></strong><small>ON-CHAIN INTELLIGENCE</small></span>
      </a>
      <span className="navigation-label">工作空间</span>
      <nav className="workspace-nav" aria-label="工作空间导航">
        {navigation.map(item => <button key={item.id} aria-label={item.label} className={active === item.id ? "active" : ""} aria-current={active === item.id ? "page" : undefined} onClick={() => onNavigate(item.id)}>
          <WorkspaceIcon name={item.icon} /><span>{item.label}</span><small>{item.number}</small>
        </button>)}
      </nav>
      <div className="sidebar-bottom">
        <div className="network-card"><span className="navigation-label">当前网络</span><div><span className="bnb-mark">◇</span><strong>BNB Smart Chain</strong><span className="network-dot" /></div><small>Mainnet <span>Chain ID 56</span></small></div>
        <div className="research-note"><WorkspaceIcon name="shield" size={17} /><div><strong>观察先于行动</strong><p>链上证据驱动的只读研究工作台</p></div></div>
        <div className="sidebar-signature"><span>SMMS / TERMINAL</span><small>v0.1</small></div>
      </div>
    </aside>
    <div className="app-main">
      <header className="topbar">
        <div className="workspace-breadcrumb"><span>工作台</span><WorkspaceIcon name="chevron" size={12} /><strong>{navigation.find(item => item.id === active)?.label}</strong></div>
        <div className="top-actions">
          <span className="chain-badge"><span>◇</span> BSC</span>
          {connected !== undefined && <span className={`connection ${connected ? "connected" : ""}`}><i />{connected ? "事件流已连接" : "事件流未连接"}</span>}
          {stream !== undefined && <span className={`connection ${liveReady ? "connected" : ""}`} title={lagLabel}><i />{chainStatus}</span>}
          {onRefresh && <button className="refresh-btn" aria-label="刷新市场数据" onClick={onRefresh} disabled={loading}><WorkspaceIcon name="refresh" size={15} /><span>{loading ? "同步中" : "刷新数据"}</span></button>}
        </div>
      </header>
      <div className="page-content">{children}</div>
      <footer className="workspace-footer"><span><WorkspaceIcon name="globe" size={12} /> BNB Smart Chain <i /> 链上情报工作台</span><span>只读研究 · 不执行交易</span></footer>
    </div>
  </div>;
}

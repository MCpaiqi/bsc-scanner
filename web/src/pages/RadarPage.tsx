import { useCallback, useEffect, useRef, useState } from "react";
import { AlphaView } from "../components/AlphaView";
import { alphaNumber, knownNumber } from "../utils/alpha";
import { tokenDisplayName } from "../utils/token";
import { CopyAddress } from "../components/CopyAddress";
import { TokenLogo } from "../components/TokenLogo";
import { WorkspaceIcon } from "../components/WorkspaceIcon";
import { WorkspaceShell, type WorkspaceView } from "../components/WorkspaceShell";
import { api, ApiError, connectRadarEvents } from "../api/client";
import type { Candidate, CandidateResponse, EventSyncState, HealthSnapshot, RadarEvent, LivePerformance as LivePerformanceData } from "../types/radar";
import { EventStream } from "../components/EventStream";
import { SourceHealth } from "../components/SourceHealth";
import { TokenDetail } from "../components/TokenDetail";
import { LivePerformance } from "../components/LivePerformance";
import { DiagnosticsPanel } from "../components/DiagnosticsPanel";

type Tab = "LIVE" | "HIGH SIGNAL" | "WATCH" | "HIGH RISK";
const tabs: Tab[] = ["LIVE", "HIGH SIGNAL", "WATCH", "HIGH RISK"];
const tabLabels: Record<Tab, string> = { LIVE: "链上发现", "HIGH SIGNAL": "高信号", WATCH: "观察中", "HIGH RISK": "高风险" };
const riskLabels: Record<string, string> = { HIGH: "高风险", CRITICAL: "严重风险", MEDIUM: "中风险", LOW: "低风险", UNKNOWN: "风险未知" };
const freshnessLabels: Record<string, string> = { FRESH: "最新观测", STALE: "数据过期", EXPIRED: "数据过期", UNKNOWN: "待确认" };
const num = alphaNumber;
const money = (value: unknown) => {
  const n = knownNumber(value);
  return n === null ? "—" : `${n < 0 ? "−" : ""}$${Math.abs(n).toLocaleString("en-US", { notation: "compact", maximumFractionDigits: 1 })}`;
};
const age = (value?: string | null) => {
  const timestamp = value ? Date.parse(value) : NaN;
  if (!Number.isFinite(timestamp)) return "—";
  const minutes = Math.max(0, Math.floor((Date.now() - timestamp) / 60000));
  return minutes < 60 ? `${minutes}m` : minutes < 1440 ? `${Math.floor(minutes / 60)}h` : `${Math.floor(minutes / 1440)}d`;
};
const riskOf = (candidate: Candidate) => String(candidate.features.risk ?? "UNKNOWN").toUpperCase();
const tabViews = { LIVE: "all", "HIGH SIGNAL": "high_signal", WATCH: "watch", "HIGH RISK": "high_risk" } as const;
const pageSize = 50;
const initialQuery = { tab: "LIVE" as Tab, sort: "priority", direction: "desc", q: "", offset: 0,
  freshness: "all", risk: "", launchpad: "", age: "", min_priority: "", smart_money: "", liquidity: "", volume: "" };

function CandidateIdentity({ candidate }: { candidate: Candidate }) {
  const label = tokenDisplayName(candidate.symbol, candidate.name, candidate.token_address);
  return <div className="radar-identity"><TokenLogo symbol={label} address={candidate.token_address} /><div className="radar-identity-text">
    <div className="radar-identity-heading"><strong title={label}>{label}</strong><span className="token-age">{age(candidate.discovered_at)}</span></div>
    <small className="radar-token-name" title={candidate.name?.trim() || "名称待补全"}>{candidate.name?.trim() || "名称待补全"}</small><CopyAddress address={candidate.token_address} compact />
  </div></div>;
}
function RiskBadge({ candidate }: { candidate: Candidate }) {
  const risk = riskOf(candidate);
  return <span className={`risk-pill risk-${risk.toLowerCase()}`}><i />{riskLabels[risk] ?? "风险未知"}</span>;
}

export default function RadarPage() {
  const [performance, setPerformance] = useState<LivePerformanceData | null>(null);
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [query, setQuery] = useState(initialQuery);
  const { tab, sort } = query;
  const changeQuery = (patch: Partial<typeof initialQuery>) => setQuery(current => ({ ...current, ...patch, offset: 0 }));
  const [result, setResult] = useState<CandidateResponse | null>(null);
  const [performanceLoading, setPerformanceLoading] = useState(true);
  const [performanceError, setPerformanceError] = useState(false);
  const [performanceAt, setPerformanceAt] = useState<Date | null>(null);
  const [view, setView] = useState<WorkspaceView>(() => {
    const requested = new URLSearchParams(window.location.search).get("view");
    return requested === "alpha" ? "ALPHA" : requested === "diagnostics" ? "DIAGNOSTICS" : "RADAR";
  });
  const [search, setSearch] = useState("");
  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(current => current.q === search.trim() ? current : { ...current, q: search.trim(), offset: 0 }), 250);
    return () => window.clearTimeout(timer);
  }, [search]);
  const [selected, setSelected] = useState<Candidate | null>(null);
  const [events, setEvents] = useState<RadarEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const [syncState, setSyncState] = useState<EventSyncState>("refreshing");
  const [detailRevision, setDetailRevision] = useState(0);
  const syncGeneration = useRef(0);
  const liveAvailable = useRef<boolean | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [health, setHealth] = useState<HealthSnapshot | null>(null);
  const [radarHealth, setRadarHealth] = useState<HealthSnapshot | null>(null);
  const [stream, setStream] = useState<HealthSnapshot | null>(null);
  const [syncedAt, setSyncedAt] = useState<Date | null>(null);
  const refreshPerformance = useRef<() => Promise<void>>(async () => {});
  const refreshCandidates = useRef<() => Promise<void>>(async () => {});
  const refresh = useCallback(() => refreshCandidates.current(), []);
  useEffect(() => {
    const controller = new AbortController();
    setCandidates([]); setResult(null); setSyncedAt(null); setError(null);
    let busy = false, queued = false;
    const load = async () => {
      if (controller.signal.aborted) return;
      if (busy) { queued = true; return; }
      busy = true; setLoading(true);
      const generation = syncGeneration.current;
      try {
        const params = new URLSearchParams({ limit: String(pageSize), offset: String(query.offset), sort,
          direction: query.direction, view: tabViews[query.tab], include_stale: "true", freshness: query.freshness });
        for (const key of ["q", "risk", "launchpad", "age", "min_priority", "smart_money", "liquidity", "volume"] as const) {
          if (query[key] !== "") params.set(key, query[key]);
        }
        const result = await api.candidates(params, controller.signal);
        if (!controller.signal.aborted && generation === syncGeneration.current) {
          if (query.offset > 0 && query.offset >= result.total) {
            setQuery(current => current === query ? { ...current, offset: Math.max(0, Math.ceil(result.total / pageSize) - 1) * pageSize } : current);
            return;
          }
          setCandidates(result.candidates ?? []); setResult(result); setSyncedAt(new Date()); setError(null);
          setSyncState(liveAvailable.current === false ? "broker_down" : liveAvailable.current === null ? "broker_unknown" : "ready");
        }
      } catch (e) {
        if (!controller.signal.aborted && generation === syncGeneration.current) {
          setError(e instanceof ApiError && e.status === 409 ? "研究快照重建中，请稍后重试。" :
            e instanceof ApiError && e.status === 503 ? "研究数据暂不可用，请稍后重试。" : "读取失败，请检查连接并重试。"); setSyncState("failed");
        }
      } finally {
        busy = false;
        if (!controller.signal.aborted) {
          setLoading(false);
          if (queued) { queued = false; void load(); }
        }
      }
    };
    refreshCandidates.current = load;
    void load();
    // Reconcile partial SSE payloads and recover events missed during reconnects.
    const timer = window.setInterval(() => void load(), 15000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [query, sort]);
  useEffect(() => {
    const controller = new AbortController();
    let busy = false;
    const load = async () => {
      if (busy || controller.signal.aborted) return;
      busy = true;
      try {
        const [a, b, c] = await Promise.all([
          api.health(controller.signal).catch(() => null),
          api.radarHealth(controller.signal).catch(() => null),
          api.streamStatus(controller.signal).catch(() => null),
        ]);
        if (!controller.signal.aborted) { setHealth(a); setRadarHealth(b); setStream(c); }
      } finally { busy = false; }
    };
    void load();
    const timer = window.setInterval(() => void load(), 15000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    let busy = false;
    const load = async () => {
      if (busy || controller.signal.aborted) return;
      busy = true; setPerformanceLoading(true);
      try {
        const data = await api.livePerformance(controller.signal);
        if (!controller.signal.aborted) { setPerformance(data); setPerformanceAt(new Date()); setPerformanceError(false); }
      } catch { if (!controller.signal.aborted) setPerformanceError(true); }
      finally { busy = false; if (!controller.signal.aborted) setPerformanceLoading(false); }
    };
    refreshPerformance.current = load; void load();
    const timer = window.setInterval(() => void load(), 15000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [detailRevision]);
  useEffect(() => {
    let timer: number | undefined;
    const disconnect = connectRadarEvents(event => {
      setEvents(current => [event, ...current.filter(item => item.event_id !== event.event_id)].slice(0, 30));
      if (!["AUDIT_STARTED", "AUDIT_FAILED", "ALERT_TRIGGERED", "SOURCE_DEGRADED"].includes(event.event_type) && timer === undefined) {
        // Candidate events omit identity and freshness; read the canonical snapshot.
        timer = window.setTimeout(() => { timer = undefined; void refresh(); }, 1000);
      }
    }, () => { setConnected(false); setSyncState("refreshing"); }, () => {
      setConnected(true); syncGeneration.current += 1; setSyncState("refreshing");
      setDetailRevision(value => value + 1); void refresh();
    }, reset => {
      syncGeneration.current += 1;
      if (reset.live_available !== undefined) liveAvailable.current = reset.live_available;
      setEvents([]); setSyncState("refreshing"); setDetailRevision(value => value + 1);
      window.clearTimeout(timer); timer = undefined;
      void refresh();
    });
    return () => { disconnect(); window.clearTimeout(timer); };
  }, [refresh]);
  const visible = candidates;
  const counts = Object.fromEntries(tabs.map(value => [value, result?.tab_counts?.[tabViews[value]]])) as Record<Tab, number | undefined>;
  const hasData = syncedAt !== null;
  const total = result?.total;
  const pendingSearch = search.trim() !== query.q;
  const resetFilters = () => { setSearch(""); setQuery(initialQuery); };
  const navigate = (next: WorkspaceView) => {
    if (next === "WALLET") { window.location.href = "/?view=wallet"; return; }
    setSelected(null); setView(next);
    window.history.replaceState(null, "", next === "RADAR" ? "/" : `/?view=${next.toLowerCase()}`);
    window.scrollTo({ top: 0, behavior: "instant" });
  };

  return <WorkspaceShell active={view} onNavigate={navigate} connected={connected} stream={stream} loading={loading} onRefresh={view === "RADAR" ? () => void refresh() : undefined}>
    {view === "DIAGNOSTICS" ? <DiagnosticsPanel health={health} radarHealth={radarHealth} stream={stream} /> : view === "ALPHA" ? <AlphaView /> : <>
      <section className="market-heading"><div><span className="section-label">MARKET INTELLIGENCE</span><h1>市场雷达<span className="heading-dot">.</span></h1><p>追踪新代币、资金流向与早期交易信号。</p></div><div className="market-heading-note"><WorkspaceIcon name="radar" size={16} /><span>链上发现<span>BNB Smart Chain / PancakeSwap</span></span></div></section>
      <section className="market-overview" aria-label="市场概览">{[
        { label: "候选代币", count: result?.universe_total, detail: "完整候选池（含过期观测）", icon: "layers", tone: "neutral" },
        { label: "筛选发现", count: counts.LIVE, detail: "当前条件下全部发现", icon: "radar", tone: "green" },
        { label: "高信号", count: counts["HIGH SIGNAL"], detail: "信号评分 ≥ 60", icon: "alpha", tone: "blue" },
        { label: "风险关注", count: counts["HIGH RISK"], detail: "高风险与严重风险", icon: "shield", tone: "amber" },
      ].map(item => <article className={`overview-card tone-${item.tone}`} key={item.label}><div><span>{item.label}</span><WorkspaceIcon name={item.icon as "layers" | "radar" | "alpha" | "shield"} size={17} /></div><strong>{hasData ? (item.count?.toLocaleString() ?? "—") : "—"}</strong><small>{item.detail}</small></article>)}</section>
      <div className="radar-layout"><main className="workspace radar-workspace">
        <div className="workspace-header"><div className="workspace-heading"><h2>代币发现</h2><span className="count-badge">{hasData ? total : "—"}</span></div><span className="last-sync">{syncedAt ? `上次同步 ${syncedAt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}` : "等待数据同步"}</span></div>
        <div className="radar-controls"><nav className="tabs" aria-label="雷达视图">{tabs.map(item => <button key={item} className={tab === item ? "active" : ""} aria-pressed={tab === item} onClick={() => changeQuery({ tab: item })}>{tabLabels[item]}<span>{hasData ? counts[item] ?? "—" : "—"}</span></button>)}</nav>
          <div className="filters"><label className="search-field"><WorkspaceIcon name="search" size={16} /><span className="sr-only">搜索代币</span><input value={search} onChange={e => setSearch(e.target.value)} placeholder="搜索名称、符号或地址…" /></label><label className="sort-field"><WorkspaceIcon name="filter" size={16} /><span className="sr-only">排序方式</span><select value={sort} onChange={e => changeQuery({ sort: e.target.value })}><option value="priority">优先级排序</option><option value="age">发现时间</option><option value="liquidity">流动性排序</option><option value="volume">5m 成交量</option></select></label></div>
        </div>
        <div className="candidate-filters" aria-label="候选筛选">
          <label>排序方向<select value={query.direction} onChange={e => changeQuery({ direction: e.target.value })}><option value="desc">从高到低 / 最新优先</option><option value="asc">从低到高 / 最早优先</option></select></label>
          <label>观测状态<select value={query.freshness} onChange={e => changeQuery({ freshness: e.target.value })}><option value="all">全部（含过期）</option><option value="fresh">最新且可用</option><option value="stale">过期观测</option><option value="unknown">待确认 / 重建中</option></select></label>
          <label>风险<select value={query.risk} onChange={e => changeQuery({ risk: e.target.value })}><option value="">全部风险</option>{Object.entries(riskLabels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
          <label>发射平台<input value={query.launchpad} onChange={e => changeQuery({ launchpad: e.target.value })} placeholder="精确平台名称" /></label>
          {([ ["age", "最大年龄（秒）", undefined], ["min_priority", "最低优先级", 100], ["smart_money", "最低聪明钱评分", 100], ["liquidity", "最低流动性 USD", undefined], ["volume", "最低 5m 成交量 USD", undefined] ] as const).map(([key, label, max]) => <label key={key}>{label}<input type="number" min="0" max={max} step={key === "age" ? "1" : "any"} value={query[key]} onChange={e => { if (e.target.value === "" || e.target.validity.valid) changeQuery({ [key]: e.target.value }); }} placeholder="不限" /></label>)}
          <button type="button" onClick={resetFilters}>重置筛选</button>
        </div>
        {pendingSearch && <p className="research-status" role="status">正在应用全局搜索…</p>}
        {result?.source_status && result.source_status !== "OK" && !error && <p className="research-status" role="status">{({ REBUILDING: "研究数据重建中，指标待确认。", STALE: "当前页含过期观测，请结合观测时间研究。", UNKNOWN: "部分来源或观测时间待确认。", PARTIAL: "当前页部分指标不可用。", EMPTY: "候选池暂无数据。", NO_MATCHES: "完整候选池中没有符合条件的结果。" } as Record<string, string>)[result.source_status] ?? "正在调整结果页…"}</p>}
        {error ? <div className="empty-state" role="alert"><span className="empty-symbol"><WorkspaceIcon name="radar" size={30} /></span><strong>暂时无法连接市场数据</strong><span>数据连接恢复后，新的链上发现会显示在这里。</span><small>{error}</small><button onClick={() => void refresh()}><WorkspaceIcon name="refresh" size={14} />重新连接</button></div>
          : loading && !candidates.length ? <div className="empty-state" role="status"><span className="empty-symbol"><WorkspaceIcon name="radar" size={30} /></span><strong>正在扫描市场视野</strong><span>正在加载已观测的链上候选…</span></div>
          : !visible.length ? <div className="empty-state"><span className="empty-symbol"><WorkspaceIcon name="search" size={28} /></span><strong>{search ? "没有匹配的代币" : "等待下一个链上发现"}</strong><span>{search ? "试试代币名称、符号或完整合约地址。" : "当前筛选下暂无候选，新的观测会出现在这里。"}</span><button onClick={resetFilters}>清除筛选</button></div>
          : <><div className="table-wrap radar-table-wrap"><table className="radar-table"><thead><tr><th>代币 <span>/ 年龄</span></th><th>市值 <span>/ 流动性</span></th><th>成交量 <span>5m / 1m</span></th><th>买入 / 卖出 <span>5m</span></th><th>聪明钱</th><th>风险 / 状态</th><th>优先级</th><th><span className="sr-only">查看详情</span></th></tr></thead><tbody>{visible.map(candidate => {
            const f = candidate.features;
            const buys = knownNumber(f.buy_count_5m), sells = knownNumber(f.sell_count_5m);
            const buyPercent = buys !== null && sells !== null && buys + sells > 0 ? Math.max(0, Math.min(100, buys / (buys + sells) * 100)) : null;
            const flow = knownNumber(f.smart_money_net_flow);
            return <tr key={`${candidate.chain}-${candidate.token_address}`} onClick={() => setSelected(candidate)}>
              <td><CandidateIdentity candidate={candidate} /></td>
              <td className="numeric"><span title={num(f.market_cap)}>{money(f.market_cap)}</span><small title={`流动性 ${num(f.liquidity)}`}>{money(f.liquidity)}</small></td>
              <td className="numeric"><span title={num(f.volume_5m)}>{money(f.volume_5m)}</span><small title={`1 分钟成交量 ${num(f.volume_1m)}`}>{money(f.volume_1m)}</small></td>
              <td className="activity-cell"><div><span className="buy-count">{num(buys)}</span><span className="metric-divider">/</span><span className="sell-count">{num(sells)}</span></div>{buyPercent !== null && <div className="trade-balance" aria-hidden="true"><i style={{ width: `${buyPercent}%` }} /></div>}<small>{num(f.unique_buyers_5m)} 位买家</small></td>
              <td className="smart-cell"><div><span className="smart-score">{num(f.smart_money_score)}</span><small>{num(f.known_smart_wallet_count)} 钱包</small></div><small className={flow === null || flow === 0 ? "" : flow > 0 ? "accent" : "negative"} title="聪明钱净流量">{flow !== null && flow > 0 ? "+" : ""}{money(flow)}</small></td>
              <td><RiskBadge candidate={candidate} /><small className={`freshness-label freshness-${String(candidate.freshness ?? "UNKNOWN").toLowerCase()}`}><i />{freshnessLabels[candidate.freshness ?? "UNKNOWN"] ?? "待确认"}</small></td>
              <td><span className={`priority-score ${(knownNumber(candidate.priority_score) ?? -1) >= 60 ? "elevated" : ""}`} title="雷达优先级评分，不代表收益概率">{num(candidate.priority_score)}</span></td>
              <td><button className="row-open" aria-label={`查看 ${tokenDisplayName(candidate.symbol, candidate.name, candidate.token_address)} 详情`} onClick={e => { e.stopPropagation(); setSelected(candidate); }}><WorkspaceIcon name="chevron" size={16} /></button></td>
            </tr>;
          })}</tbody></table></div>
          <div className="radar-mobile-cards">{visible.map(candidate => <article className="radar-mobile-card" key={candidate.token_address} onClick={() => setSelected(candidate)}><div className="mobile-candidate-heading"><CandidateIdentity candidate={candidate} /><button className="row-open" aria-label={`查看 ${tokenDisplayName(candidate.symbol, candidate.name, candidate.token_address)} 详情`} onClick={e => { e.stopPropagation(); setSelected(candidate); }}><WorkspaceIcon name="chevron" /></button></div><div className="mobile-candidate-metrics"><div><small>市值</small><strong>{money(candidate.features.market_cap)}</strong></div><div><small>5m 成交量</small><strong>{money(candidate.features.volume_5m)}</strong></div><div><small>聪明钱评分</small><strong>{num(candidate.features.smart_money_score)}</strong></div></div><div className="mobile-candidate-footer"><RiskBadge candidate={candidate} /><span>优先级 <b>{num(candidate.priority_score)}</b></span></div></article>)}</div>
          <div className="table-footer"><span>显示 {query.offset + 1}–{query.offset + visible.length} / {total ?? "—"} 个候选</span><span>全局筛选 · 动态观测结果</span></div></>}
        <nav className="candidate-pagination" aria-label="候选分页"><button type="button" disabled={loading || pendingSearch || query.offset === 0} onClick={() => setQuery(current => ({ ...current, offset: Math.max(0, current.offset - pageSize) }))}>上一页</button><span aria-live="polite">第 {Math.floor(query.offset / pageSize) + 1} 页 / {total === undefined ? "—" : Math.max(1, Math.ceil(total / pageSize))} 页 · 每页 {pageSize} 条</span><button type="button" disabled={loading || pendingSearch || total === undefined || query.offset + pageSize >= total} onClick={() => setQuery(current => ({ ...current, offset: current.offset + pageSize }))}>下一页</button></nav>
      </main><aside className="context-rail" aria-label="市场观察"><EventStream events={events} connected={connected} syncState={syncState} onRetry={() => { setSyncState("refreshing"); void refresh(); }} /><SourceHealth health={health} radarHealth={radarHealth} stream={stream} /><LivePerformance data={performance} loading={performanceLoading} failed={performanceError} readAt={performanceAt} onRetry={() => void refreshPerformance.current()} /></aside></div>
    </>}
    {selected && <TokenDetail candidate={selected} refreshKey={detailRevision} onClose={() => setSelected(null)} />}
  </WorkspaceShell>;
}

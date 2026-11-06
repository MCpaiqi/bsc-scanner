import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { AlphaCandidate, AlphaCandidateDetail, AlphaStats } from "../types/radar";
import { alphaNumber, knownNumber, outcomeMultiplier } from "../utils/alpha";
import { safeExternalUrl } from "../utils/address";
import { tokenDisplayName } from "../utils/token";
import { CopyAddress } from "./CopyAddress";
import { TokenLogo } from "./TokenLogo";
import { TerminalIcon } from "./TerminalIcon";
import "./alpha-terminal.css";

const age = (seconds: number) => seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m`;
const dexName = (dex: string | null) => dex === "pancakeswap" ? "PancakeSwap V2" : dex || "DEX pending";
const volume = (row?: AlphaCandidate) => {
  const value = alphaNumber(row?.features.volume_quote);
  const unit = row?.identity.quote_symbol;
  return value === "—" ? value : `${value} ${unit === "WBNB" ? "BNB" : unit || "quote"}`;
};
export function ScoreBadge({ value }: { value: unknown }) {
  const score = knownNumber(value);
  return <span title="实验排序分数 · 并非获利概率" className={`score-badge score-${score === null || score < 30 ? "muted" : score < 50 ? "positive" : score < 70 ? "strong" : "attention"}`}>{alphaNumber(value)}</span>;
}
export function RiskPill({ risk }: { risk?: string }) {
  const status = ["LOW", "MEDIUM", "HIGH"].includes(risk || "") ? risk! : "UNKNOWN";
  return <span className={`risk-pill risk-${status.toLowerCase()}`} title={status === "UNKNOWN" ? "未知 · risk evidence unavailable" : status}>{status === "UNKNOWN" ? "? UNKNOWN" : status === "MEDIUM" ? "MED" : status}</span>;
}
function Identity({ row, addresses = true }: { row: AlphaCandidate; addresses?: boolean }) {
  const i = row.identity;
  const label = tokenDisplayName(i.symbol, i.name, i.token);
  const name = i.name?.trim() || "Metadata pending";
  return <div className="terminal-identity"><TokenLogo address={i.token} symbol={i.symbol?.trim() || i.name?.trim()} imageUrl={i.image_url} /><div className="identity-text"><strong title={label}>{label}</strong><span className="token-name" title={name}>{name}</span>{addresses && <CopyAddress address={i.token} compact />}<small title={`Pair: ${i.pair}`}>{dexName(i.dex)} · {i.quote_symbol || "Quote pending"}</small></div></div>;
}

export function AlphaDetail({ selected, detail, onClose }: { selected: AlphaCandidate; detail: AlphaCandidateDetail | null; onClose: () => void }) {
  const active = detail?.latest ?? selected;
  const dexUrl = safeExternalUrl(active.identity.dex_url);
  const coverage = active.outcome.future_coverage;
  const covered = active.outcome.tracking_complete === true && coverage != null && typeof coverage === "object" && "status" in coverage && coverage.status === "VERIFIED";
  return <div className="alpha-detail" aria-label="Alpha detail">
    <header className="drawer-heading"><Identity row={active} addresses={false} /><button className="icon-button" aria-label="关闭 Alpha 详情" onClick={onClose}>×</button></header>
    <div className="drawer-contracts"><label>代币合约 · TOKEN</label><CopyAddress address={active.identity.token} full compact /><label>交易对合约 · PAIR</label><CopyAddress address={active.identity.pair} kind="pair" full compact />{dexUrl && <a href={dexUrl} target="_blank" rel="noreferrer">在 DexScreener 查看 <TerminalIcon name="external" /></a>}<small>{active.score_version} · 观测快照 {age(active.observation_seconds)}</small></div>
    {active.input_revision != null && <p>研究修订 {active.input_revision} · {active.task_status === "PENDING_INPUT" ? "重建中 · 等待覆盖与输入验证" : "当前观测版本"}{active.snapshot_id != null && <a href={`/api/alpha/revisions/${active.snapshot_id}/lineage`} target="_blank" rel="noreferrer"> 查看来源</a>}</p>}
    <section><h3>核心指标 <span>OVERVIEW</span></h3><div className="overview-grid"><div><small>Alpha 分数</small><ScoreBadge value={active.alpha_score} /></div><div><small>代币年龄</small><b>{age(active.current_age_seconds)}</b></div><div><small>风险状态</small><RiskPill risk={active.risk_status} /></div><div><small>独立买家</small><b>{alphaNumber(active.features.unique_buyers)}</b></div><div><small>成交量</small><b>{volume(active)}</b></div><div><small>买卖比</small><b>{alphaNumber(active.features.buy_sell_ratio)}</b></div></div><details><summary>查看评分构成</summary><div className="score-components">{Object.entries(active.components).map(([key, value]) => <p key={key}><span>{key}</span><b>{alphaNumber(value)}</b></p>)}</div></details></section>
    <section><h3>早期信号 <span>EARLY SIGNAL</span></h3><div className="signal-table-wrap"><table className="early-metrics"><thead><tr><th>指标</th>{[30,60,180,300].map(n => <th key={n}>{age(n)}</th>)}</tr></thead><tbody>{["Score", "Buyers", "B/S", "Volume"].map(metric => <tr key={metric}><td>{metric}</td>{[30,60,180,300].map(n => { const item = detail?.snapshots.find(s => s.observation_seconds === n); return <td key={n}>{metric === "Score" ? alphaNumber(item?.alpha_score) : metric === "Volume" ? volume(item) : alphaNumber(item?.features[metric === "Buyers" ? "unique_buyers" : "buy_sell_ratio"])}</td>; })}</tr>)}</tbody></table></div></section>
    <section><h3>信号依据 <span>WHY IT SURFACED</span></h3><ul className="reason-list">{active.positive_reasons.map(r => <li className="positive" key={r}>+ {r}</li>)}{active.negative_reasons.map(r => <li className="negative" key={r}>− {r}</li>)}{active.warnings.map(r => <li className="warning" key={r}>? {r}</li>)}</ul>{!active.positive_reasons.length && !active.negative_reasons.length && !active.warnings.length && <p className="reasons-empty">暂无可用信号依据</p>}</section>
    <section><h3>影子表现 <span>SHADOW EVALUATION</span></h3><p role="status">{covered ? "完整覆盖已验证" : "覆盖待验证"} · 缺失数据时目标结果保持未知</p><dl className="shadow-values"><dt>价格基准</dt><dd>{String(active.outcome.price_basis ?? "—")}</dd><dt>入场倍数</dt><dd title={String(active.outcome.entry_vwap ?? "Entry unavailable")}>{active.outcome.entry_vwap == null ? "—" : "1.00x"}</dd><dt>入场价格</dt><dd className="entry-price">{String(active.outcome.entry_vwap ?? "—")}</dd><dt>当前倍数</dt><dd>{outcomeMultiplier(active.outcome.current_return)}</dd><dt>最高可实现倍数</dt><dd>{outcomeMultiplier(active.outcome.max_realisable_return)}</dd><dt>最高瞬时倍数</dt><dd>{outcomeMultiplier(active.outcome.max_tick_return)}</dd></dl><div className="shadow-milestones">{[2,3,5,10].map(n => <span key={n} data-reached={active.outcome[`reached_${n}x`] === true ? "true" : undefined} title={active.outcome[`reached_${n}x`] == null ? "Unknown" : active.outcome[`reached_${n}x`] ? "Reached" : "Not reached"}>{n}x {active.outcome[`reached_${n}x`] === true ? "●" : active.outcome[`reached_${n}x`] === false ? "○" : "—"}</span>)}</div><small>倍数 = 1 + 收益率 · 观测窗口 1 小时 · 仅用于评估</small></section>
  </div>;
}

function Drawer({ children, onClose }: { children: React.ReactNode; onClose: () => void }) {
  const ref = useRef<HTMLElement>(null);
  const closeRef = useRef(onClose); closeRef.current = onClose;
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    ref.current?.focus();
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeRef.current();
      if (event.key === "Tab") {
        const items = Array.from(ref.current?.querySelectorAll<HTMLElement>('button,a[href],input,summary,[tabindex="0"]') || []).filter(el => !el.hasAttribute("disabled"));
        const first = items[0], last = items[items.length - 1];
        if (event.shiftKey && (document.activeElement === first || document.activeElement === ref.current)) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }
    };
    document.addEventListener("keydown", key);
    return () => { document.removeEventListener("keydown", key); previous?.focus({ preventScroll: true }); };
  }, []);
  return <div className="alpha-drawer-backdrop" onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}><aside className="alpha-drawer" ref={ref} tabIndex={-1} role="dialog" aria-modal="true" aria-label="Alpha terminal detail">{children}</aside></div>;
}

export function AlphaView() {
  const [rows, setRows] = useState<AlphaCandidate[]>([]);
  const [stats, setStats] = useState<AlphaStats | null>(null);
  const [selected, setSelected] = useState<AlphaCandidate | null>(null);
  const [detail, setDetail] = useState<AlphaCandidateDetail | null>(null);
  const [detailStatus, setDetailStatus] = useState<"loading" | "error" | "success">("loading");
  const [detailError, setDetailError] = useState("");
  const [retry, setRetry] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [ageFilter, setAgeFilter] = useState(0);
  const [scoreFilter, setScoreFilter] = useState(false);
  const [sort, setSort] = useState<{ key: "alpha" | "age" | "buyers" | "volume"; descending: boolean }>({ key: "alpha", descending: true });
  useEffect(() => {
    const controller = new AbortController(); let busy = false;
    const refresh = async () => {
      if (busy) return;
      busy = true;
      try {
        const [result, summary] = await Promise.all([api.alphaCandidates(controller.signal), api.alphaStats(controller.signal)]);
        if (!controller.signal.aborted) { setRows(result.candidates ?? []); setStats(summary); setError(null); }
      } catch (e) { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "Alpha API unavailable"); }
      finally { busy = false; if (!controller.signal.aborted) setLoading(false); }
    };
    void refresh(); const id = window.setInterval(() => void refresh(), 15000);
    return () => { controller.abort(); window.clearInterval(id); };
  }, []);
  useEffect(() => {
    if (!selected) return;
    const controller = new AbortController(); let busy = false;
    setDetail(null); setDetailStatus("loading");
    const refresh = async () => {
      if (busy) return;
      busy = true; setDetailStatus("loading");
      try {
        // Pair identity keeps a multi-pair token's history scoped to this row.
        const result = await api.alphaCandidate(selected.identity.pair, controller.signal);
        if (!controller.signal.aborted) { setDetail(result); setDetailStatus("success"); }
      } catch (e) {
        if (!controller.signal.aborted) { setDetail(null); setDetailStatus("error"); setDetailError(e instanceof Error ? e.message : "Alpha detail unavailable"); }
      } finally { busy = false; }
    };
    void refresh(); const id = window.setInterval(() => void refresh(), 15000);
    return () => { controller.abort(); window.clearInterval(id); };
  }, [selected, retry]);
  const sortValue = (row: AlphaCandidate) => knownNumber(sort.key === "alpha" ? row.alpha_score : sort.key === "age" ? row.current_age_seconds : row.features[sort.key === "buyers" ? "unique_buyers" : "volume_quote"]);
  const visible = rows.filter(row => `${row.identity.symbol || ""} ${row.identity.name || ""} ${row.identity.token} ${row.identity.pair}`.toLowerCase().includes(search.trim().toLowerCase()) && (!ageFilter || row.current_age_seconds < ageFilter) && (!scoreFilter || (knownNumber(row.alpha_score) ?? -1) >= 30)).sort((a,b) => { const x = sortValue(a), y = sortValue(b); return x === null ? y === null ? 0 : 1 : y === null ? -1 : (x-y)*(sort.descending ? -1 : 1); });
  const heading = (key: typeof sort.key, title: string) => <th aria-sort={sort.key === key ? sort.descending ? "descending" : "ascending" : "none"}><button onClick={() => setSort({ key, descending: sort.key === key ? !sort.descending : key !== "age" })}>{title} {sort.key === key ? sort.descending ? "↓" : "↑" : "↕"}</button></th>;
  const open = (row: AlphaCandidate) => { setDetail(null); setDetailStatus("loading"); setSelected(row); };
  return <main className="workspace alpha-workspace terminal-workspace">
    <div className="terminal-title"><div><span className="section-label">ALPHA INTELLIGENCE / BSC</span><h2>发现早期 Alpha<span className="terminal-network">PancakeSwap V2</span></h2><p>跟踪新代币的早期买入信号，建立可验证的观察列表。</p></div><span className="terminal-mode"><i /> SHADOW MODE<span>实验评估</span></span></div>
    <div className="terminal-summary">
      <div className="terminal-counts">
        <div><span>累计评分 <small>SCORED</small></span><b>{stats?.scored_tokens ?? "—"}</b><p>已进入评分流程的代币</p></div>
        <div className="summary-highlight"><span>新鲜候选 <small>FRESH NOW</small></span><b>{loading ? "—" : rows.length}</b><p>当前可跟踪的早期信号</p></div>
        <div><span>高分信号 <small>HIGH ALPHA</small></span><b>{stats?.high_score_tokens ?? "—"}</b><p>累计高 Alpha 评分代币</p></div>
        <div><span>完整观测 <small>MATURED 1H</small></span><b>{stats?.matured_1h ?? "—"}</b><p>1 分钟样本，完整覆盖后续 1 小时</p></div>
      </div>
      <div className="shadow-performance"><div className="shadow-performance-label"><strong>影子评估</strong><small>1H SHADOW PERFORMANCE</small></div><div className="shadow-performance-metrics">{["2","3","5","10"].map(n => { const result = stats?.overall[n]; return <span key={n}><b>{n}x</b>{result && result.denominator >= 20 ? <><em>{result.rate == null ? "—" : `${(result.rate*100).toFixed(1)}%`}</em><small>{result.hits}/{result.denominator} 命中</small></> : <><em>—</em><small>{result ? "N<20 · 样本不足" : "等待观测"}</small></>}</span>; })}</div><span className="shadow-performance-note">仅用于信号评估</span></div>
    </div>
    <div className="terminal-feed-heading"><div><h3>候选观察列表</h3><span>{loading ? "加载中" : `${visible.length} 个结果`}</span></div><span className={`terminal-refresh ${error ? "has-error" : ""}`}><i />{error ? "连接异常" : "每 15 秒自动刷新"}</span></div>
    <div className="terminal-toolbar"><label className="terminal-search"><TerminalIcon name="search" /><input aria-label="Search symbol / name / token / pair" placeholder="搜索名称、代币或交易对地址" value={search} onChange={e => setSearch(e.target.value)} /></label><div className="age-filters" aria-label="代币年龄筛选">{[[0,"全部年龄"],[300,"< 5m"],[900,"< 15m"]].map(([n,label]) => <button key={n} aria-pressed={ageFilter === n} onClick={() => setAgeFilter(Number(n))}>{label}</button>)}</div><button className="score-filter" aria-pressed={scoreFilter} onClick={() => setScoreFilter(!scoreFilter)}><span aria-hidden="true">{scoreFilter ? "✓" : "+"}</span> Alpha ≥ 30</button></div>
    {error ? <div role="alert" className="empty-state terminal-empty"><span className="terminal-empty-mark">!</span><strong>暂时无法获取 Alpha 数据</strong><p>{error}</p><small>保留当前筛选条件，系统将自动重试。</small></div> : loading ? <div role="status" className="empty-state terminal-empty"><span className="terminal-empty-mark">···</span><strong>正在加载 Alpha…</strong><p>正在同步代币与早期信号</p></div> : <>
    <div className="terminal-table-wrap"><table className="terminal-table"><caption className="sr-only">Alpha 候选观察列表；点击列标题排序，打开详情查看信号依据。</caption><thead><tr><th>代币 / TOKEN</th>{heading("age","年龄")}{heading("alpha","ALPHA")}<th className="momentum-col">动量</th>{heading("buyers","买家")}<th>买卖比</th>{heading("volume","成交量")}<th>风险</th><th>影子表现 <small>当前 / 最高可实现</small></th><th><span className="sr-only">Detail</span></th></tr></thead><tbody>{visible.map(row => <tr key={row.identity.pair} onClick={() => open(row)} className={selected?.identity.pair === row.identity.pair ? "selected-row" : ""}><td><Identity row={row} /></td><td>{age(row.current_age_seconds)}</td><td><ScoreBadge value={row.alpha_score} /></td><td className="momentum-col"><span>{alphaNumber(row.components.momentum)} <small>/ 25</small></span>{knownNumber(row.components.momentum) !== null && <div className="momentum-track"><i style={{ width: `${Math.max(0,Math.min(100,Number(row.components.momentum)*4))}%` }} /></div>}</td><td>{alphaNumber(row.features.unique_buyers)}</td><td>{alphaNumber(row.features.buy_sell_ratio)}</td><td>{volume(row)}</td><td><RiskPill risk={row.risk_status} /></td><td className="shadow-cell">{outcomeMultiplier(row.outcome.current_return)}<small>{outcomeMultiplier(row.outcome.max_realisable_return)}</small></td><td><button className="alpha-open terminal-icon-button" aria-label={`Open ${tokenDisplayName(row.identity.symbol, row.identity.name, row.identity.token)} detail`} onClick={e => {e.stopPropagation(); open(row);}}><TerminalIcon name="chevron" /></button></td></tr>)}</tbody></table></div>
    <div className="terminal-cards">{visible.map(row => <article className="candidate-card" key={row.identity.pair} onClick={() => open(row)}><div className="card-heading"><Identity row={row} /><div className="card-score"><ScoreBadge value={row.alpha_score} /><small>{age(row.current_age_seconds)}</small></div></div><div className="card-metrics"><span><small>独立买家</small>{alphaNumber(row.features.unique_buyers)}</span><span><small>买卖比</small>{alphaNumber(row.features.buy_sell_ratio)}</span><span><small>成交量</small>{volume(row)}</span></div><div className="card-foot"><RiskPill risk={row.risk_status} /><span>当前 <b>{outcomeMultiplier(row.outcome.current_return)}</b></span><button className="terminal-icon-button" aria-label={`Open ${tokenDisplayName(row.identity.symbol, row.identity.name, row.identity.token)} detail`} onClick={e => {e.stopPropagation();open(row);}}>详情 <TerminalIcon name="chevron" /></button></div></article>)}</div>
    {!visible.length && <div className="empty-state terminal-empty"><span className="terminal-empty-mark"><TerminalIcon name="search" /></span><strong>暂无匹配的新鲜 Alpha 候选</strong><p>{search || ageFilter || scoreFilter ? "试着调整筛选条件，寻找更多信号。" : "新候选出现后会自动显示在这里。"}</p>{Boolean(search || ageFilter || scoreFilter) && <button onClick={() => { setSearch(""); setAgeFilter(0); setScoreFilter(false); }}>清除筛选</button>}</div>}
    <div className="terminal-footnote"><span>— 表示数据尚不可用</span><span>Alpha 为实验排序分数，并非获利概率</span></div></>}
    {selected && <Drawer onClose={() => setSelected(null)}><div className="alpha-detail-status" aria-live="polite">{detailStatus === "loading" ? "正在刷新详情…" : detailStatus === "success" ? "详情已更新 · 每 15 秒刷新" : <span role="alert">详情加载失败：{detailError} <button onClick={() => setRetry(value => value+1)}>重试</button></span>}</div>{detailStatus !== "error" ? <AlphaDetail selected={selected} detail={detail} onClose={() => setSelected(null)} /> : <button onClick={() => setSelected(null)}>关闭详情</button>}</Drawer>}
  </main>;
}

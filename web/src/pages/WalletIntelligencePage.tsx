import { Fragment, useState } from "react";
import { api } from "../api/client";
import { WorkspaceShell } from "../components/WorkspaceShell";
import { WorkspaceIcon } from "../components/WorkspaceIcon";

const text = (value: unknown) => value === null || value === undefined ? "未知" : String(value);
const short = (value: string) => value.length > 18 ? `${value.slice(0, 9)}...${value.slice(-7)}` : value;
const labels: Record<string, string> = {
  bought_token: "买入代币",
  funded_wallet: "资金关联",
  followed_wallet: "跟随钱包",
  traded_together: "共同交易",
  synchronized_entry: "同步入场",
  coordinated_exit: "协同退出",
  repeated_co_buy: "重复共买",
  BUY: "买入",
  SELL: "卖出",
};
const label = (value: unknown) => labels[String(value)] ?? text(value);

export default function WalletIntelligencePage({ onBack }: { onBack: () => void }) {
  const [address, setAddress] = useState("");
  const [profile, setProfile] = useState<Record<string, unknown> | null>(null);
  const [clusters, setClusters] = useState<Record<string, unknown> | null>(null);
  const [graph, setGraph] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const load = async () => {
    if (loading || !address.trim()) return;
    setLoading(true); setError(null);
    try {
      const [nextProfile, nextClusters, nextGraph] = await Promise.all([
        api.walletProfile(address.trim()), api.walletClusters(address.trim()), api.walletGraph(address.trim()),
      ]);
      setProfile(nextProfile); setClusters(nextClusters); setGraph(nextGraph);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "无法加载钱包情报"); }
    finally { setLoading(false); }
  };
  const alpha = profile?.alpha_components as Record<string, unknown> | undefined;
  const relationships = (clusters?.relationships as Array<Record<string, unknown>> | undefined) ?? [];
  const nodes = (graph?.nodes as Array<Record<string, unknown>> | undefined) ?? [];
  return <WorkspaceShell active="WALLET" onNavigate={view => {
    if (view === "RADAR") onBack();
    else if (view !== "WALLET") window.location.href = `/?view=${view.toLowerCase()}`;
  }}>
    <main className="intelligence-page">
      <div className="workspace-header"><div><span className="section-label">WALLET INTELLIGENCE</span><h2>钱包情报<span className="heading-dot">.</span></h2></div><span className="last-sync">行为画像 / 关联图谱</span></div>
      <div className="filters"><label><span>钱包地址</span><input value={address} onChange={(event) => setAddress(event.target.value)} onKeyDown={event => { if (event.key === "Enter") void load(); }} placeholder="输入 BSC 钱包地址，探索链上足迹…" /></label><button className="refresh-btn intel-load" onClick={() => void load()} disabled={loading || !address.trim()}><WorkspaceIcon name="search" size={14} />{loading ? "查询中" : "分析钱包"}</button></div>
      {error && <div className="empty-state"><strong>钱包不可用</strong><span>{error}</span></div>}
      {!profile && !error && <div className="empty-state"><span className="empty-symbol"><WorkspaceIcon name="wallet" size={30} /></span><strong>每个钱包，都有自己的链上轨迹</strong><span>输入钱包地址，查看行为画像、关联聚类与交易历史。</span></div>}
      {profile && <>
        <section className="intel-grid">
          <div className="detail-block"><h3>钱包画像</h3><dl><dt>钱包</dt><dd title={text(profile.wallet)}>{short(text(profile.wallet))}</dd><dt>聪明钱评分</dt><dd className="accent">{text(profile.smart_money_score)}</dd><dt>已实现 ROI</dt><dd className="accent">{text(profile.realized_roi ?? profile.roi)}</dd><dt>未实现 ROI</dt><dd>{text(profile.unrealized_roi)}</dd><dt>胜率</dt><dd>{text(profile.win_rate)}</dd><dt>持仓周期</dt><dd>{text(profile.holding_period)}秒</dd><dt>链上活跃度</dt><dd>{text(profile.chain_activity ?? profile.number_trades)}</dd></dl></div>
          <div className="detail-block"><h3>阿尔法评分</h3>{alpha ? <dl>{Object.entries(alpha).map(([key, value]) => <Fragment key={key}><dt>{labels[key] ?? key.replaceAll("_", " ")}</dt><dd>{text(value)}</dd></Fragment>)}</dl> : <div className="empty-inline">暂无评分解释</div>}<p className="intel-explain">历史技能 40% · 当前行为 25% · 网络影响力 20% · 风险调整 15%</p></div>
          <div className="detail-block"><h3>钱包聚类</h3>{((clusters?.clusters as Array<Record<string, unknown>> | undefined) ?? []).map((cluster) => <div className="cluster-row" key={text(cluster.cluster_id)}><strong>{short(text(cluster.cluster_id))}</strong><span>{((cluster.wallets as string[]) ?? []).length} 个钱包</span><small>{text(cluster.confidence)} 置信度</small></div>)}{!((clusters?.clusters as Array<unknown> | undefined) ?? []).length && <div className="empty-inline">暂无关联聚类</div>}</div>
        </section>
        <section className="detail-block intel-wide"><h3>关联代币 <small>{nodes.filter((node) => node.type === "token").length} 个</small></h3><div className="token-chips">{nodes.filter((node) => node.type === "token").map((node) => <span key={text(node.id)}>{short(text(node.id))}</span>)}</div></section>
        <section className="detail-block intel-wide"><h3>近期活动 <small>{relationships.length} 条关系</small></h3>{graph?.truncated === true && <div className="empty-inline">图谱已截断，当前结果受服务端上限保护</div>}<div className="activity-list">{relationships.slice(0, 20).map((item, index) => <div className="activity-row" key={`${text(item.kind)}-${index}`}><span className="event-kind">{label(item.kind)}</span><span>{short(text(item.source))} → {short(text(item.target))}</span><small>{text((item.evidence as Record<string, unknown> | undefined)?.timestamp ?? "已观测")}</small></div>)}</div>{!relationships.length && <div className="empty-inline">当前时间点暂无关联活动</div>}</section>
        <section className="detail-block intel-wide"><h3>交易历史 <small>{nodes.filter((node) => node.type === "trade").length} 条</small></h3><div className="activity-list">{nodes.filter((node) => node.type === "trade").slice(-20).reverse().map((node, index) => <div className="activity-row" key={`trade-${index}`}><span className="event-kind">{label(node.side)}</span><span>{short(text(node.token))} · {text(node.value)}</span><small>{text(node.timestamp)}</small></div>)}</div></section>
      </>}
    </main>
  </WorkspaceShell>;
}

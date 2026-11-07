import { useEffect, useRef, useState } from "react";
import { CopyAddress } from "./CopyAddress";
import { api, ApiError } from "../api/client";
import { tokenDisplayName } from "../utils/token";
import type { Candidate, DetailPayload, EarlyBuyer } from "../types/radar";

const value = (input: unknown) => input === null || input === undefined || input === "UNKNOWN" ? "未知" : String(input);
export function TokenDetail({ candidate, onClose, refreshKey = 0 }: { candidate: Candidate; onClose: () => void; refreshKey?: number }) {
  const panelRef = useRef<HTMLElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    panelRef.current?.focus();
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); closeRef.current(); }
      if (event.key !== "Tab") return;
      const items = panelRef.current?.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], input, select, textarea');
      if (!items?.length) { event.preventDefault(); return; }
      const first = items[0], last = items[items.length - 1];
      if (event.shiftKey && (document.activeElement === first || document.activeElement === panelRef.current)) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", handleKey);
    return () => { document.removeEventListener("keydown", handleKey); document.body.style.overflow = overflow; previous?.focus(); };
  }, []);
  const [detail, setDetail] = useState<DetailPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [favorite, setFavorite] = useState(false);
  const [note, setNote] = useState("");
  const [annotationLoading, setAnnotationLoading] = useState(true);
  const [readAt, setReadAt] = useState<Date | null>(null);
  const [detailFailed, setDetailFailed] = useState(false);
  const [retryRevision, setRetryRevision] = useState(0);
  const [tags, setTags] = useState("");
  const [alertScore, setAlertScore] = useState(80);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setDetailFailed(false);
    setDetail(null);
    setReadAt(null);
    let busy = false;
    const load = async (strict = false) => {
      if (busy || controller.signal.aborted) return;
      busy = true; setLoading(true);
      try {
        const [full, early, smart, risk] = await Promise.all([
          api.candidate(candidate.chain, candidate.token_address, controller.signal),
          api.earlyBuyers(candidate.token_address, controller.signal),
          api.smartMoney(candidate.token_address, controller.signal),
          api.risk(candidate.token_address, controller.signal),
        ]);
        if (!controller.signal.aborted) {
          setDetail({ candidate: full, earlyBuyers: early.early_buyers ?? [], smartMoney: smart.smart_money, risk: risk.risk });
          setDetailFailed(false); setReadAt(new Date());
        }
      } catch (error) {
        if (!controller.signal.aborted) {
          if (strict || error instanceof ApiError && error.status === 409) setDetail(null);
          setDetailFailed(true);
        }
      } finally { busy = false; if (!controller.signal.aborted) setLoading(false); }
    };
    void load(true);
    const timer = window.setInterval(() => void load(), 15000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [candidate.chain, candidate.token_address, refreshKey, retryRevision]);
  useEffect(() => {
    const controller = new AbortController();
    setAnnotationLoading(true); setFavorite(false); setNote(""); setTags("");
    api.annotation(candidate.chain, candidate.token_address, controller.signal).catch(() => null).then(annotation => {
      if (controller.signal.aborted) return;
      setFavorite(Boolean(annotation?.favorite));
      setNote(typeof annotation?.note === "string" ? annotation.note : "");
      setTags(Array.isArray(annotation?.tags) ? annotation.tags.map(String).join(", ") : "");
      setAnnotationLoading(false);
    });
    return () => controller.abort();
  }, [candidate.chain, candidate.token_address]);
  const current = detail?.candidate ?? candidate;
  const label = tokenDisplayName(current.symbol, current.name, current.token_address);
  return <div className="detail-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><section ref={panelRef} tabIndex={-1} className="detail-panel" role="dialog" aria-modal="true" aria-label={`${label} 详情`}><header className="detail-header"><div><span className="section-label">代币详情</span><h2>{label} <small>{current.token_address.slice(0, 8)}...{current.token_address.slice(-6)}</small></h2><p className="radar-token-name" title={current.name?.trim() || "名称待补全"}>{current.name?.trim() || "名称待补全"}</p></div><button className="icon-button" onClick={onClose} aria-label="关闭详情">×</button></header>{loading && !detail || annotationLoading ? <div className="detail-loading">正在加载已验证数据…</div> : detailFailed && !detail ? <div className="detail-loading" role="alert">详情数据未同步<button type="button" onClick={() => setRetryRevision(value => value + 1)}>重新加载详情</button></div> : <div className="detail-content"><p className="research-status" role={detailFailed ? "alert" : "status"}>{detailFailed ? "更新失败，以下为上次读取的数据，可能已过期。" : loading ? "正在更新研究数据…" : `上次读取 ${readAt?.toLocaleTimeString() ?? "待确认"} · 观测 ${current.observed_at ? new Date(current.observed_at).toLocaleString() : "待确认"} · ${current.source_status ?? "来源待确认"}`}</p><div className="detail-block"><h3>IDENTITY</h3><p>Token Contract</p><CopyAddress address={current.token_address} full /><p>Pair Contract</p><CopyAddress address={current.pair_address} kind="pair" full /></div><div className="detail-tabs"><span className="active">市场</span><span>聪明钱</span><span>早期买家</span><span>风险</span><span>证据</span><span>信号</span><span>审计历史</span></div><div className="detail-grid"><div className="detail-block"><h3>市场</h3><dl><dt>发射平台</dt><dd>{value(current.launchpad)}</dd><dt>年龄</dt><dd>{current.discovered_at ? new Date(current.discovered_at).toLocaleString() : "未知"}</dd><dt>流动性</dt><dd>{value(current.features.liquidity)}</dd><dt>5 分钟交易量</dt><dd>{value(current.features.volume_5m)}</dd></dl></div><div className="detail-block"><h3>聪明钱</h3><dl><dt>评分</dt><dd className="accent">{value(current.features.smart_money_score)}</dd><dt>钱包数量</dt><dd>{value(current.features.known_smart_wallet_count)}</dd><dt>净流量</dt><dd>{value(detail?.smartMoney?.smart_money_net_flow)}</dd></dl></div><div className="detail-block"><h3>风险</h3><dl><dt>总体风险</dt><dd className={`risk-${String(current.features.risk ?? "unknown").toLowerCase()}`}>{value(current.features.risk)}</dd><dt>数据状态</dt><dd>{value(detail?.risk ? "MATERIALIZED" : "UNKNOWN")}</dd></dl></div></div><div className="detail-block workflow"><h3>工作流</h3><label><input type="checkbox" checked={favorite} onChange={(event) => { const next = event.target.checked; setFavorite(next); void api.saveAnnotation(candidate.chain, candidate.token_address, { favorite: next, note, tags: tags.split(",").map((tag) => tag.trim()).filter(Boolean) }); }} /> 收藏</label><textarea value={note} onChange={(event) => setNote(event.target.value)} placeholder="备注" /><input value={tags} onChange={(event) => setTags(event.target.value)} placeholder="标签，用逗号分隔" /><button onClick={() => void api.saveAnnotation(candidate.chain, candidate.token_address, { favorite, note, tags: tags.split(",").map((tag) => tag.trim()).filter(Boolean) })}>保存备注</button><label>提醒评分 <input type="number" min="0" max="100" value={alertScore} onChange={(event) => setAlertScore(Number(event.target.value))} /></label><button onClick={() => void api.createAlertRule({ name: `${label} signal`, chain: candidate.chain, conditions: [{ field: "smart_money_score", operator: ">=", value: alertScore }], notification_channels: ["in-app", "browser"] })}>创建提醒规则</button></div><div className="detail-block early-buyers"><h3>早期买家 <small>{detail?.earlyBuyers.length ?? 0} 个</small></h3>{detail?.earlyBuyers.length ? <div className="buyer-table"><div className="buyer-head"><span>排名</span><span>钱包</span><span>入场时间</span><span>ROI</span><span>机器人</span></div>{detail.earlyBuyers.map((buyer: EarlyBuyer) => <div className="buyer-row" key={`${buyer.rank}-${buyer.wallet}`}><span>{buyer.rank}</span><span title={buyer.wallet}>{buyer.wallet.slice(0, 7)}...{buyer.wallet.slice(-5)}</span><span>{new Date(buyer.entry_time).toLocaleString()}</span><span>{value(buyer.roi ?? buyer.realized_pnl)}</span><span>{value(buyer.bot_status)}</span></div>)}</div> : <div className="empty-inline">暂无已验证的早期买家数据</div>}</div><div className="detail-grid"><div className="detail-block"><h3>证据</h3><dl><dt>证据状态</dt><dd>{value(current.data_status)}</dd><dt>新鲜度</dt><dd>{current.data_freshness_seconds == null ? "未知" : `${current.data_freshness_seconds}s`}</dd></dl></div><div className="detail-block"><h3>信号</h3><dl><dt>类别</dt><dd>{value(current.latest_signal_class)}</dd><dt>评分</dt><dd className="accent">{value(current.latest_signal_score)}</dd><dt>优先级</dt><dd>{value(current.priority_score)}</dd></dl></div><div className="detail-block"><h3>审计历史</h3><div className="empty-inline">审计生命周期会在事件流中实时展示。</div></div></div></div>}</section></div>;
}

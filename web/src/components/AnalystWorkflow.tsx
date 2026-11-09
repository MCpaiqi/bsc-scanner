import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api/client";
import { alertRequestId } from "../utils/alert-request";

type Draft = { favorite: boolean; note: string; tags: string };
const blank: Draft = { favorite: false, note: "", tags: "" };
const contents = (draft: Draft) => ({ favorite: draft.favorite, note: draft.note,
  tags: [...new Set(draft.tags.split(",").map(tag => tag.trim()).filter(Boolean))].sort() });
const signature = (draft: Draft) => JSON.stringify(contents(draft));


export function AnalystWorkflow({ chain, token: rawToken }: { chain: string; token: string }) {
  const token = chain === "bsc" ? rawToken.trim().toLowerCase() : rawToken;
  const [draft, setDraft] = useState<Draft>(blank);
  const draftRef = useRef(draft); draftRef.current = draft;
  const [baseline, setBaseline] = useState(signature(blank));
  const [revision, setRevision] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [readError, setReadError] = useState(false);
  const [saving, setSaving] = useState(false);
  const saveBusy = useRef(false);
  const [saveResult, setSaveResult] = useState<"idle" | "saved" | "error" | "conflict">("idle");
  const [latest, setLatest] = useState<Draft | null>(null);
  const alive = useRef(true);
  const preserveReadDraft = useRef(false);
  const readController = useRef<AbortController | null>(null);
  const writes = useRef(new Set<AbortController>());
  const [scope, setScope] = useState<"TOKEN" | "GLOBAL">("TOKEN");
  const [threshold, setThreshold] = useState("80");
  const [alertBusy, setAlertBusy] = useState(false);
  const alertPending = useRef(false);
  const request = useRef<{ signature: string; id: string } | null>(null);
  const [alertResult, setAlertResult] = useState<{ message: string; failed: boolean; signature?: string } | null>(null);
  const score = threshold.trim() === "" ? null : Number(threshold);
  const validScore = score !== null && Number.isFinite(score) && score >= 0 && score <= 100;
  const alertBody = { name: `${scope === "TOKEN" ? token : "BSC global"} smart money >= ${score}`,
    chain, scope, token_address: scope === "TOKEN" ? token : null,
    conditions: [{ field: "smart_money_score", operator: ">=", value: score }], notification_channels: ["in-app", "browser"] };
  const alertSignature = JSON.stringify(alertBody);
  const dirty = signature(draft) !== baseline;

  const load = async (keepDraft = false) => {
    keepDraft = keepDraft || preserveReadDraft.current;
    preserveReadDraft.current = keepDraft;
    readController.current?.abort();
    const controller = new AbortController(); readController.current = controller;
    setLoading(true); setReadError(false); setRevision(null);
    try {
      let data: Record<string, unknown>;
      try { data = await api.annotation(chain, token, controller.signal); }
      catch (error) { if (error instanceof ApiError && error.status === 404) data = { revision: 0 }; else throw error; }
      if (controller.signal.aborted || !alive.current) return;
      if (typeof data.revision !== "number" || !Number.isInteger(data.revision) || data.revision < 0) throw new Error("invalid annotation revision");
      const next = { favorite: Boolean(data.favorite), note: typeof data.note === "string" ? data.note : "",
        tags: Array.isArray(data.tags) ? data.tags.map(String).join(", ") : "" };
      setRevision(data.revision); setBaseline(signature(next));
      if (keepDraft) { setLatest(next); setSaveResult("idle"); }
      else { setDraft(next); setLatest(null); }
      preserveReadDraft.current = false;
    } catch { if (!controller.signal.aborted && alive.current) setReadError(true); }
    finally { if (!controller.signal.aborted && alive.current) setLoading(false); }
  };
  useEffect(() => {
    alive.current = true; void load();
    return () => { alive.current = false; readController.current?.abort(); writes.current.forEach(controller => controller.abort()); };
  }, [chain, token]);

  const save = async () => {
    if (saveBusy.current || revision === null || loading || readError || !dirty || saveResult === "conflict") return;
    saveBusy.current = true; setSaving(true); setSaveResult("idle");
    const sent = draftRef.current;
    const controller = new AbortController(); writes.current.add(controller);
    try {
      const result = await api.saveAnnotation(chain, token, { ...contents(sent), expected_revision: revision }, controller.signal);
      if (!alive.current || controller.signal.aborted) return;
      if (typeof result.revision !== "number" || result.revision <= revision) throw new Error("invalid save revision");
      setRevision(result.revision); setBaseline(signature(sent)); setLatest(null); setSaveResult("saved");
    } catch (error) {
      if (alive.current && !controller.signal.aborted) setSaveResult(error instanceof ApiError && error.status === 409 ? "conflict" : "error");
    } finally {
      writes.current.delete(controller); saveBusy.current = false;
      if (alive.current) setSaving(false);
    }
  };
  const createAlert = async () => {
    if (alertPending.current || !validScore || alertResult?.signature === alertSignature) return;
    alertPending.current = true; setAlertBusy(true);
    const sentScope = scope;
    const controller = new AbortController(); writes.current.add(controller);
    setAlertResult({ message: `正在创建${sentScope === "TOKEN" ? "当前代币" : "全局 BSC"}提醒…`, failed: false });
    try {
      if (request.current?.signature !== alertSignature) request.current = { signature: alertSignature, id: alertRequestId(alertSignature) };
      const result = await api.createAlertRule({ ...alertBody, request_id: request.current.id }, controller.signal);
      if (!alive.current || controller.signal.aborted) return;
      if (!Number.isInteger(result.id) || result.scope !== sentScope || result.token_address !== alertBody.token_address) throw new Error("invalid rule scope response");
      setAlertResult({ message: `已创建${sentScope === "TOKEN" ? "当前代币" : "全局 BSC"}提醒 #${result.id}`, failed: false, signature: alertSignature });
    } catch {
      if (alive.current && !controller.signal.aborted) setAlertResult({ message: "提醒创建失败，重试将使用同一请求编号。", failed: true });
    } finally {
      writes.current.delete(controller); alertPending.current = false;
      if (alive.current) setAlertBusy(false);
    }
  };
  return <section className="analyst-workflow" aria-label="研究工作流">
    <div className="detail-block workflow"><h3>当前代币备注与收藏</h3>
      <p className="workflow-scope">BNB Smart Chain · {token}</p>
      {loading && <p role="status">正在读取备注…</p>}
      {readError && <p role="alert">备注读取失败，暂不能保存。<button type="button" onClick={() => void load(Boolean(latest) || dirty)}>重新读取备注</button></p>}
      <label><input type="checkbox" checked={draft.favorite} disabled={loading || readError} onChange={event => setDraft(current => ({ ...current, favorite: event.target.checked }))} /> 收藏（随备注一起保存）</label>
      <label className="workflow-wide">备注<textarea value={draft.note} disabled={loading || readError} onChange={event => setDraft(current => ({ ...current, note: event.target.value }))} placeholder="备注" /></label>
      <label className="workflow-wide">标签<input value={draft.tags} disabled={loading || readError} onChange={event => setDraft(current => ({ ...current, tags: event.target.value }))} placeholder="标签，用逗号分隔" /></label>
      <button type="button" disabled={saving || loading || readError || revision === null || !dirty || saveResult === "conflict"} onClick={() => void save()}>{saving ? "保存中…" : "保存备注"}</button>
      <p role={saveResult === "error" || saveResult === "conflict" ? "alert" : "status"}>
        {saveResult === "conflict" ? "保存冲突：已有其他更新，草稿已保留。" : saveResult === "error" ? "备注保存失败，草稿已保留，可以重试。" : saving ? "正在提交备注…" : dirty ? saveResult === "saved" ? "上次提交已保存；当前修改尚未保存。" : "当前修改尚未保存。" : saveResult === "saved" ? "备注已保存。" : "无未保存修改。"}
      </p>
      {saveResult === "conflict" && <button type="button" disabled={loading} onClick={() => void load(true)}>读取最新版本并保留草稿</button>}
      {latest && <div className="workflow-latest"><p>最新已保存版本（请核对后保存当前草稿）</p><p>收藏：{latest.favorite ? "是" : "否"} · 标签：{latest.tags || "无"}</p><pre>{latest.note || "无备注"}</pre></div>}
    </div>
    <div className="detail-block workflow"><h3>站内提醒</h3>
      <label>作用范围<select aria-label="提醒作用范围" value={scope} onChange={event => setScope(event.target.value as "TOKEN" | "GLOBAL")}><option value="TOKEN">仅当前代币</option><option value="GLOBAL">全部 BSC 代币（全局）</option></select></label>
      <p className="workflow-scope">{scope === "TOKEN" ? `仅匹配当前合约：${token}` : "全局规则会匹配所有 BSC 代币。"}</p>
      <label>聪明钱评分至少<input aria-label="提醒评分" type="number" min="0" max="100" step="any" value={threshold} onChange={event => setThreshold(event.target.value)} /></label>
      {!validScore && <p role="alert">请输入 0 到 100 的有限评分。</p>}
      <button type="button" disabled={alertBusy || !validScore || alertResult?.signature === alertSignature} onClick={() => void createAlert()}>{alertBusy ? "创建中…" : "创建提醒规则"}</button>
      {alertResult && <p role={alertResult.failed ? "alert" : "status"}>{alertResult.message}</p>}
      <p className="workflow-scope">提醒在站内事件流展示，不执行交易。</p>
    </div>
  </section>;
}

import { useEffect, useId, useRef, useState, useSyncExternalStore } from "react";
import { api, ApiError } from "../api/client";
import { analystSession } from "../api/analyst-session";

export function useAnalystAccess() {
  const authorized = useSyncExternalStore(analystSession.subscribe, analystSession.snapshot, () => false);
  const [enabled, setEnabled] = useState<boolean>();
  const [checking, setChecking] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const request = useRef<AbortController | null>(null);
  const revision = useRef(0);

  const check = async (candidate?: string) => {
    const current = ++revision.current;
    request.current?.abort();
    const controller = new AbortController(); request.current = controller;
    setChecking(true); setError(null); setMessage(null);
    try {
      const result = await api.analystAccess(candidate, controller.signal);
      if (controller.signal.aborted || current !== revision.current) return;
      setEnabled(result.enabled);
      if (!result.enabled) setMessage("此工作台当前只开放浏览。");
      else if (candidate !== undefined && !result.authorized) setError("密钥无效，请重新输入。");
    } catch (error) {
      if (controller.signal.aborted || current !== revision.current) return;
      setError(error instanceof ApiError && error.code === "ANALYST_INSECURE_TRANSPORT"
        ? "请通过 HTTPS 或本机地址解锁研究编辑。"
        : candidate === undefined ? "授权状态读取失败，请检查连接后重试。" : "授权未成功，请检查连接后重试。");
    } finally {
      if (!controller.signal.aborted && current === revision.current) setChecking(false);
    }
  };
  useEffect(() => {
    void check();
    return () => { revision.current += 1; request.current?.abort(); };
  }, []);
  const lock = () => {
    revision.current += 1; request.current?.abort(); api.lockAnalyst();
    setChecking(false); setError(null); setMessage("研究编辑已锁定。");
  };
  return { authorized, enabled, checking, error, message, canWrite: enabled === true && authorized && !checking && !error,
    retry: () => check(), unlock: (candidate: string) => check(candidate), lock };
}

export function AnalystAccess({ access }: { access: ReturnType<typeof useAnalystAccess> }) {
  const [credential, setCredential] = useState("");
  const id = useId();
  return <div className="detail-block analyst-access">
    <h3>研究编辑权限</h3>
    <p role={access.error ? "alert" : "status"}>{access.checking ? "正在核对编辑权限…" : access.error ?? access.message ??
      (access.authorized ? "研究编辑已解锁，仅本页有效。" : "浏览模式：解锁后可保存备注与创建提醒。")}</p>
    {access.authorized && <button type="button" onClick={access.lock}>锁定研究编辑</button>}
    {!access.authorized && access.enabled === true && <form method="post" aria-label="分析员解锁" onSubmit={event => {
      event.preventDefault();
      const candidate = credential.trim();
      if (!candidate || access.checking) return;
      setCredential(""); void access.unlock(candidate);
    }}>
      <label htmlFor={id}>分析员访问密钥<input id={id} aria-label="分析员访问密钥" type="password" autoComplete="current-password"
        value={credential} disabled={access.checking} onChange={event => setCredential(event.target.value)} /></label>
      <button type="submit" disabled={access.checking || !credential.trim()}>{access.checking ? "核对中…" : "解锁研究编辑"}</button>
    </form>}
    {access.error && <button type="button" disabled={access.checking} onClick={() => void access.retry()}>重试授权状态</button>}
  </div>;
}

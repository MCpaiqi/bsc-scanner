import type { AlphaCandidateDetail, AlphaResponse, AlphaStats, CandidateResponse, DetailPayload, EarlyBuyer, HealthSnapshot, Candidate, RadarEvent, LivePerformance } from "../types/radar";
import { analystSession } from "./analyst-session";

export class ApiError extends Error {
  constructor(readonly status: number, readonly dataStatus?: string, readonly code?: string) { super(`HTTP ${status}`); }
}

export type AnalystAccessState = { enabled: boolean; authorized: boolean };

function analystHeaders(credential: string): Record<string, string> {
  const location = globalThis.location;
  if (!location || location.protocol !== "https:" && !["localhost", "127.0.0.1", "[::1]"].includes(location.hostname)) {
    throw new ApiError(0, undefined, "ANALYST_INSECURE_TRANSPORT");
  }
  return { Authorization: `Bearer ${credential}` };
}

async function analystAccess(candidate?: string, signal?: AbortSignal): Promise<AnalystAccessState> {
  const version = candidate === undefined ? analystSession.generation() : analystSession.begin();
  const credential = candidate ?? analystSession.credential();
  const response = await fetch("/api/radar/access", { signal, cache: "no-store", credentials: "omit", redirect: "error",
    headers: { Accept: "application/json", ...(credential ? analystHeaders(credential) : {}) } });
  if (!response.ok) throw new ApiError(response.status);
  const result: unknown = await response.json();
  if (!result || typeof result !== "object" || !("enabled" in result) || !("authorized" in result) ||
      typeof result.enabled !== "boolean" || typeof result.authorized !== "boolean") {
    throw new ApiError(0, undefined, "ANALYST_STATUS_INVALID");
  }
  const valid = result.enabled && result.authorized && credential !== null && !signal?.aborted;
  const authorized = candidate !== undefined
    ? valid && analystSession.accept(candidate, version)
    : valid && analystSession.generation() === version && analystSession.snapshot();
  if (!authorized) analystSession.clear(version);
  return { enabled: result.enabled, authorized };
}

async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, { headers: { Accept: "application/json" }, signal });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new ApiError(response.status, body?.detail?.data_status);
  }
  return response.json() as Promise<T>;
}

async function write<T>(path: string, method: string, body: Record<string, unknown>, signal?: AbortSignal): Promise<T> {
  const credential = analystSession.credential();
  const version = analystSession.generation();
  if (credential === null) throw new ApiError(401, undefined, "ANALYST_UNAUTHORIZED");
  const response = await fetch(path, { method, headers: { "Content-Type": "application/json", Accept: "application/json", ...analystHeaders(credential) },
    body: JSON.stringify(body), signal, credentials: "omit", redirect: "error" });
  if (!response.ok) {
    const data = await response.json().catch(() => null);
    if (response.status === 401 || data?.detail?.code === "ANALYST_READ_ONLY") analystSession.clear(version);
    throw new ApiError(response.status, data?.detail?.data_status, data?.detail?.code);
  }
  return response.json() as Promise<T>;
}

export const api = {
  analystAccess,
  lockAnalyst: () => analystSession.clear(),
  candidates: (params: URLSearchParams, signal?: AbortSignal) => get<CandidateResponse>(`/api/radar/candidates?${params}`, signal),
  candidate: (chain: string, token: string, signal?: AbortSignal) => get<Candidate>(`/api/radar/candidates/${encodeURIComponent(chain)}/${encodeURIComponent(token)}`, signal),
  earlyBuyers: (token: string, signal?: AbortSignal) => get<{ early_buyers?: EarlyBuyer[]; data_status?: string }>(`/api/radar/tokens/${encodeURIComponent(token)}/early-buyers`, signal),
  smartMoney: (token: string, signal?: AbortSignal) => get<{ smart_money: Record<string, unknown> | null }>(`/api/radar/tokens/${encodeURIComponent(token)}/smart-money`, signal),
  risk: (token: string, signal?: AbortSignal) => get<{ risk: Record<string, unknown> | null }>(`/api/radar/tokens/${encodeURIComponent(token)}/risk`, signal),
  health: (signal?: AbortSignal) => get<HealthSnapshot>("/health", signal),
  diagnostics: (signal?: AbortSignal) => get<HealthSnapshot>("/api/radar/diagnostics", signal),
  radarHealth: (signal?: AbortSignal) => get<HealthSnapshot>("/api/radar/health", signal),
  streamStatus: (signal?: AbortSignal) => get<HealthSnapshot>("/api/bsc/stream/status", signal),
  livePerformance: (signal?: AbortSignal) => get<LivePerformance>("/api/radar/live-performance", signal),
  alphaCandidates: (signal?: AbortSignal) => get<AlphaResponse>("/api/alpha/candidates", signal),
  alphaCandidate: (tokenOrPair: string, signal?: AbortSignal) => get<AlphaCandidateDetail>(`/api/alpha/candidates/${tokenOrPair}`, signal),
  alphaStats: (signal?: AbortSignal) => get<AlphaStats>("/api/alpha/stats", signal),
  annotation: (chain: string, token: string, signal?: AbortSignal) => get<Record<string, unknown>>(`/api/radar/annotations/${encodeURIComponent(chain)}/${encodeURIComponent(token)}`, signal),
  saveAnnotation: (chain: string, token: string, body: Record<string, unknown>, signal?: AbortSignal) => write<Record<string, unknown>>(`/api/radar/annotations/${encodeURIComponent(chain)}/${encodeURIComponent(token)}`, "PUT", body, signal),
  createAlertRule: (body: Record<string, unknown>, signal?: AbortSignal) => write<{ id: number; scope: "TOKEN" | "GLOBAL"; token_address: string | null }>("/api/radar/alert-rules", "POST", body, signal),
  walletProfile: (address: string, chain = "bsc", signal?: AbortSignal) => get<Record<string, unknown>>(`/api/wallet/${encodeURIComponent(address)}/profile?chain=${encodeURIComponent(chain)}`, signal),
  walletClusters: (address: string, chain = "bsc", signal?: AbortSignal) => get<Record<string, unknown>>(`/api/wallet/${encodeURIComponent(address)}/clusters?chain=${encodeURIComponent(chain)}`, signal),
  walletGraph: (address: string, chain = "bsc", signal?: AbortSignal) => get<Record<string, unknown>>(`/api/wallet/${encodeURIComponent(address)}/graph?chain=${encodeURIComponent(chain)}`, signal),
};

export function connectRadarEvents(onEvent: (event: RadarEvent) => void, onError?: () => void, onOpen?: () => void, onReset?: (reset: import("../types/radar").StreamRecovery) => void): () => void {
  const eventTypes = ["CANDIDATE_DISCOVERED", "CANDIDATE_UPDATED", "SMART_MONEY_ENTRY", "EARLY_BUYER_UPDATE", "RISK_CHANGED", "SIGNAL_CHANGED", "AUDIT_STARTED", "AUDIT_COMPLETED", "AUDIT_FAILED", "ALERT_TRIGGERED", "SOURCE_DEGRADED"];
  const source = new EventSource(`/api/radar/events?event_types=${eventTypes.join(",")}`);
  const seen = new Set<string>();
  const record = (input: unknown): input is Record<string, unknown> => input !== null && typeof input === "object" && !Array.isArray(input);
  const reset = (message: MessageEvent<string>) => {
    let data: import("../types/radar").StreamRecovery = { reason: "INVALID_CONTROL" };
    try {
      const envelope: unknown = JSON.parse(message.data);
      if (record(envelope) && envelope.event_type === "STREAM_RESET" && record(envelope.data) && typeof envelope.data.reason === "string") data = envelope.data as unknown as import("../types/radar").StreamRecovery;
    } catch { /* Request a canonical resnapshot for an invalid control. */ }
    seen.clear();
    onReset?.(data);
  };
  const handler = (message: MessageEvent<string>) => {
    let parsed: unknown;
    try { parsed = JSON.parse(message.data); } catch { onReset?.({ reason: "INVALID_EVENT" }); return; }
    if (!record(parsed) || parsed.event_type !== message.type || parsed.chain !== "bsc" ||
        typeof parsed.event_id !== "string" || !parsed.event_id || parsed.event_id.length > 256 || /[\r\n]/.test(parsed.event_id) ||
        typeof parsed.occurred_at !== "string" || !Number.isFinite(Date.parse(parsed.occurred_at)) || !record(parsed.data)) {
      onReset?.({ reason: "INVALID_EVENT" }); return;
    }
    if (seen.has(parsed.event_id)) return;
    if (seen.size >= 1024) seen.delete(seen.values().next().value!);
    seen.add(parsed.event_id);
    onEvent(parsed as unknown as RadarEvent);
  };
  eventTypes.forEach((type) => source.addEventListener(type, handler));
  source.addEventListener("STREAM_RESET", reset);
  source.onopen = () => onOpen?.();
  source.onerror = () => onError?.();
  return () => { source.onopen = null; source.onerror = null; eventTypes.forEach((type) => source.removeEventListener(type, handler)); source.removeEventListener("STREAM_RESET", reset); seen.clear(); source.close(); };
}

import type { AlphaCandidateDetail, AlphaResponse, AlphaStats, CandidateResponse, DetailPayload, EarlyBuyer, HealthSnapshot, Candidate, RadarEvent, LivePerformance } from "../types/radar";

async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, { headers: { Accept: "application/json" }, signal });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json() as Promise<T>;
}

export const api = {
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
  saveAnnotation: (chain: string, token: string, body: Record<string, unknown>) => fetch(`/api/radar/annotations/${encodeURIComponent(chain)}/${encodeURIComponent(token)}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then((response) => { if (!response.ok) throw new Error(`HTTP ${response.status}`); return response.json() as Promise<Record<string, unknown>>; }),
  createAlertRule: (body: Record<string, unknown>) => fetch("/api/radar/alert-rules", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then((response) => { if (!response.ok) throw new Error(`HTTP ${response.status}`); return response.json(); }),
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

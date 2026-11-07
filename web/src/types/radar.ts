export type Risk = "UNKNOWN" | "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
export type EvidenceStatus = "OBSERVED" | "DEGRADED" | "UNKNOWN" | "ORPHANED" | string;

export interface Candidate {
  chain: string;
  token_address: string;
  pair_address?: string | null;
  symbol: string | null;
  name?: string | null;
  launchpad: string;
  discovered_at?: string | null;
  latest_seen_at?: string | null;
  priority_score: number;
  features: Record<string, unknown>;
  data_status: EvidenceStatus;
  evidence: Record<string, unknown>;
  latest_signal_class: string;
  latest_signal_score: number | string;
  as_of?: string | null;
  data_freshness_seconds?: number | null;
  source_status?: string;
  observed_at?: string | null;
  updated_at?: string | null;
  freshness?: "FRESH" | "STALE" | "EXPIRED" | "UNKNOWN" | string;
}

export interface SourceHealthRecord {
  source?: string;
  name?: string;
  status?: string;
  freshness?: string;
  freshness_seconds?: number | null;
  error_count?: number;
  error_rate?: number;
  rate_limited_count?: number;
  latency_ms?: number | null;
  last_error?: string | null;
  details?: Record<string, unknown>;
}
export interface HealthMetrics {
  chain?: Record<string, unknown>;
  ingestion?: Record<string, unknown>;
  candidates?: Record<string, unknown>;
  audit?: Record<string, unknown>;
  providers?: Record<string, Record<string, unknown>>;
  sse?: Record<string, unknown>;
  [key: string]: unknown;
}
export interface CandidateResponse { candidates: Candidate[]; count: number; total: number; as_of?: string; observed_at?: string; updated_at?: string; freshness?: string; }
export interface EarlyBuyer {
  wallet: string; rank: number; entry_time: string; entry_delay_seconds?: number;
  entry_usd?: string | null; roi?: string | null; realized_pnl?: string | null;
  unrealized_pnl?: string | null; buy_count?: number; sell_count?: number;
  wallet_smart_money_score?: number | null; historical_performance?: string | null; bot_status?: string | null;
}
export interface DetailPayload { candidate: Candidate; earlyBuyers: EarlyBuyer[]; smartMoney: Record<string, unknown> | null; risk: Record<string, unknown> | null; }
export type RadarEventType = "CANDIDATE_DISCOVERED" | "CANDIDATE_UPDATED" | "SMART_MONEY_ENTRY" | "EARLY_BUYER_UPDATE" | "RISK_CHANGED" | "SIGNAL_CHANGED" | "AUDIT_STARTED" | "AUDIT_COMPLETED" | "AUDIT_FAILED" | "ALERT_TRIGGERED" | "SOURCE_DEGRADED";
export interface RadarEvent { event_id: string; event_type: RadarEventType; chain: string; token_address?: string; token?: string; occurred_at: string; data: Record<string, unknown>; }
export interface AlphaCandidate {
  identity: { chain: string; token: string; pair: string; symbol: string | null; name: string | null; image_url: string | null; quote_symbol: string | null; dex: string | null; dex_url: string | null };
  pair_created_at: string; snapshot_at: string; observation_seconds: number; current_age_seconds: number; alpha_score: number | null;
  snapshot_id?: number; input_revision?: number; supersedes_id?: number | null; task_status?: string;
  risk_status: Risk; score_version: string; status: string; components: Record<string, unknown>;
  features: Record<string, unknown>; positive_reasons: string[]; negative_reasons: string[];
  warnings: string[]; outcome: Record<string, unknown>;
}
export interface AlphaResponse { mode: string; shadow_mode: boolean; candidates: AlphaCandidate[]; }
export interface AlphaCandidateDetail { latest: AlphaCandidate; snapshots: AlphaCandidate[]; }
export interface AlphaStats { scored_tokens: number; high_score_tokens: number; matured_1h: number; overall: Record<string, { hits: number; denominator: number; rate: number | null }>; high_score: Record<string, { hits: number; denominator: number; rate: number | null }>; }
export interface HealthSnapshot {
  runtime_worker_status?: { status?: string; live_ready?: boolean; block_lag?: number | null; lag?: number | null; ingestion_phase?: string; } | null;
  worker_heartbeat_age_seconds?: number | null;
  block_lag?: number | null;
  status?: string;
  database?: string;
  redis?: string;
  bsc_rpc?: string;
  bsc_stream?: string;
  market_provider?: string;
  sources?: Record<string, SourceHealthRecord>;
  source_health?: Record<string, SourceHealthRecord>;
  metrics?: HealthMetrics;
  observability?: { status?: string; [key: string]: unknown };
  freshness?: string;
  [key: string]: unknown;
}
export interface LivePerformance { mode: "FORWARD_OBSERVATION"; horizons: Record<string, Record<string, { sample_count: number; median_return: number | null; win_rate: number | null; "2x_rate": number | null; rug_rate: number | null; max_drawdown: number | null }>>; }

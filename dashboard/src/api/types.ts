// Mirrors the gateway's /admin/api responses (src/switchboard/api/routes/admin_*.py).

export type Window = "1h" | "24h" | "7d";

export interface Totals {
  requests: number;
  errors: number;
  error_rate: number;
  p50_ms: number;
  p95_ms: number;
  cost_usd: number;
  prompt_tokens: number;
  completion_tokens: number;
  cache_hit_rate: number;
}

export interface SeriesPoint {
  ts: string;
  requests: number;
  errors: number;
  p95_ms: number;
  cost_usd: number;
  cache_hits: number;
}

export interface Overview {
  window: Window;
  totals: Totals;
  series: SeriesPoint[];
}

export interface ModelRow {
  model: string;
  requests: number;
  error_rate: number;
  p50_ms: number;
  p95_ms: number;
  cost_usd: number;
  cache_hit_rate: number;
}

export type CacheStatus = "miss" | "exact" | "semantic" | "bypass";

export interface RequestLog {
  id: number;
  request_id: string;
  created_at: string;
  api_key_id: string | null;
  model_alias: string;
  provider: string | null;
  upstream_model: string | null;
  status_code: number;
  cache: CacheStatus;
  attempts: number;
  stream: boolean;
  latency_ms: number;
  ttft_ms: number | null;
  prompt_tokens: number;
  completion_tokens: number;
  cost_usd: string | number;
  error: string | null;
}

export interface RequestPage {
  items: RequestLog[];
  next_before_id: number | null;
}

export interface RequestFilters {
  model?: string;
  status?: "ok" | "error";
  cache?: CacheStatus;
}

export type BreakerState = "closed" | "open" | "half_open";

export interface ProviderHealth {
  provider: string;
  model: string;
  state: BreakerState;
}

export interface EvalRunSummary {
  id: string;
  suite: string;
  model: string;
  created_at: string;
  pass_rate: number;
  duration_s: number;
  total_cases: number;
  passed_cases: number;
}

export interface EvalScore {
  type: string;
  passed: boolean;
  score: number;
  reason: string;
}

export interface EvalCase {
  case_id: string;
  passed: boolean;
  critical: boolean;
  output: string;
  latency_ms: number;
  cost_usd: number | null;
  error: string | null;
  scores: EvalScore[];
}

export interface EvalRunDetail extends EvalRunSummary {
  result: { cases: EvalCase[] };
}

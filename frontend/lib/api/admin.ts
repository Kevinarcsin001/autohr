"use client";

import { apiClient } from "./client";

// ============================================================================
// 类型（与 backend/app/schemas/admin.py 对应）
// ============================================================================

export type LLMScope = "extractor" | "scorer" | "reasoning" | "interview";
export type StatsRange = "7d" | "30d";

export interface LLMConfigOut {
  id: string;
  team_id: string | null;
  scope: LLMScope;
  primary: string;
  fallback: string | null;
  model_overrides: Record<string, string> | null;
  timeout_seconds: number | null;
  circuit_breaker_failures: number | null;
  updated_at: string;
}

export interface LLMConfigUpsertPayload {
  scope: LLMScope;
  primary: string;
  fallback?: string | null;
  model_overrides?: Record<string, string> | null;
  timeout_seconds?: number | null;
  circuit_breaker_failures?: number | null;
  team_id?: string | null;
}

export interface LLMConfigUpsertResponse {
  config: LLMConfigOut;
  created: boolean;
}

export interface LLMConfigListResponse {
  items: LLMConfigOut[];
}

export interface StatsSummary {
  range: StatsRange;
  total_calls: number;
  success_count: number;
  failed_count: number;
  success_rate: number;
  total_tokens_in: number;
  total_tokens_out: number;
  total_cost_cny: number;
  p50_latency_ms: number | null;
  p95_latency_ms: number | null;
  p99_latency_ms: number | null;
}

export interface StatsDimensionItem {
  key: string;
  total_calls: number;
  success_count: number;
  failed_count: number;
  total_tokens_in: number;
  total_tokens_out: number;
  total_cost_cny: number;
}

export interface StatsByDimension {
  dimension: string;
  items: StatsDimensionItem[];
}

export interface StatsTimePoint {
  timestamp: string;
  total_calls: number;
  success_count: number;
  failed_count: number;
  total_cost_cny: number;
}

export interface StatsTimeSeries {
  range: StatsRange;
  granularity: "hour" | "day";
  points: StatsTimePoint[];
}

export interface StatsResponse {
  summary: StatsSummary;
  by_scope: StatsByDimension;
  by_adapter: StatsByDimension;
  time_series: StatsTimeSeries;
}

// ============================================================================
// API
// ============================================================================

export async function listLLMConfigsApi(): Promise<LLMConfigListResponse> {
  const { data } = await apiClient.get<LLMConfigListResponse>(
    "/api/admin/llm-configs",
  );
  return data;
}

export async function upsertLLMConfigApi(
  payload: LLMConfigUpsertPayload,
): Promise<LLMConfigUpsertResponse> {
  const { data } = await apiClient.post<LLMConfigUpsertResponse>(
    "/api/admin/llm-configs",
    payload,
  );
  return data;
}

export async function deleteLLMConfigApi(configId: string): Promise<void> {
  await apiClient.delete(`/api/admin/llm-configs/${configId}`);
}

export async function getStatsApi(range: StatsRange): Promise<StatsResponse> {
  const { data } = await apiClient.get<StatsResponse>("/api/admin/stats", {
    params: { range },
  });
  return data;
}

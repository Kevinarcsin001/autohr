"use client";

import { apiClient } from "./client";

/**
 * 筛选 API client（任务 24 复用）：HR 改判 + 列表 + 触发流水线。
 */

// ============================================================================
// 类型（与 backend/app/schemas/screening.py 对应）
// ============================================================================

export interface OverrideRequest {
  new_disqualified: boolean;
  new_reasons?: string[] | null;
  reason: string;
}

export interface ScreeningResultOut {
  id: string;
  job_id: string;
  candidate_id: string;
  disqualified: boolean;
  reasons: string[] | null;
  manually_overridden: boolean;
}

export interface OverrideResponse {
  screening_result: ScreeningResultOut;
  override_id: string;
}

export interface PipelineRunRequest {
  job_id: string;
  candidate_ids: string[];
}

export interface PipelineRunResponse {
  run_id: string;
  job_id: string;
  total: number;
}

// ============================================================================
// API
// ============================================================================

export async function overrideScreeningResultApi(
  screeningResultId: string,
  payload: OverrideRequest,
): Promise<OverrideResponse> {
  const { data } = await apiClient.patch<OverrideResponse>(
    `/api/screening/results/${screeningResultId}/override`,
    payload,
  );
  return data;
}

export async function listScreeningOverridesApi(
  screeningResultId: string,
): Promise<unknown[]> {
  const { data } = await apiClient.get<unknown[]>(
    `/api/screening/results/${screeningResultId}/overrides`,
  );
  return data;
}

export async function triggerPipelineApi(
  payload: PipelineRunRequest,
): Promise<PipelineRunResponse> {
  const { data } = await apiClient.post<PipelineRunResponse>(
    "/api/screening/pipeline",
    payload,
  );
  return data;
}

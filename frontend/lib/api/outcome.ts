"use client";

import { apiClient } from "./client";

/**
 * 录用结果（效果回流）API：ground truth 落库 + 校准报告 + 漏斗统计。
 */

export type FinalStatus =
  | "hired"
  | "probation_passed"
  | "rejected"
  | "withdrawn";

export const FINAL_STATUS_LABELS: Record<FinalStatus, string> = {
  hired: "已录用",
  probation_passed: "已转正",
  rejected: "未录用",
  withdrawn: "候选人放弃",
};

export interface CandidateJobOutcome {
  id: string;
  job_id: string;
  candidate_id: string;
  final_status: FinalStatus;
  note: string | null;
  decided_at: string | null;
  created_at: string | null;
}

export interface CalibrationBucket {
  score_min: number;
  score_max: number;
  total: number;
  hired: number;
  rejected: number;
  other: number;
  hire_rate: number | null;
}

export interface CalibrationReport {
  job_id: string | null;
  buckets: CalibrationBucket[];
  total_with_outcome: number;
}

export interface ChannelQuality {
  source_type: string;
  total: number;
  screened_pass: number;
  hired: number;
  pass_rate: number | null;
}

export interface FunnelStats {
  job_id: string | null;
  total_pool: number;
  screened_pass: number;
  needs_review: number;
  disqualified: number;
  scored: number;
  interviewed: number;
  hired: number;
  channels: ChannelQuality[];
}

export async function putOutcomeApi(
  jobId: string,
  candidateId: string,
  payload: { final_status: FinalStatus; note?: string | null },
): Promise<CandidateJobOutcome> {
  const { data } = await apiClient.put<CandidateJobOutcome>(
    `/api/jobs/${jobId}/candidates/${candidateId}/outcome`,
    payload,
  );
  return data;
}

export async function getOutcomeApi(
  jobId: string,
  candidateId: string,
): Promise<CandidateJobOutcome | null> {
  const { data } = await apiClient.get<CandidateJobOutcome | null>(
    `/api/jobs/${jobId}/candidates/${candidateId}/outcome`,
  );
  return data;
}

export async function getCalibrationApi(
  jobId: string,
): Promise<CalibrationReport> {
  const { data } = await apiClient.get<CalibrationReport>(
    `/api/jobs/${jobId}/calibration`,
  );
  return data;
}

export async function getFunnelApi(jobId?: string): Promise<FunnelStats> {
  const { data } = await apiClient.get<FunnelStats>("/api/dashboard/funnel", {
    params: jobId ? { job_id: jobId } : undefined,
  });
  return data;
}

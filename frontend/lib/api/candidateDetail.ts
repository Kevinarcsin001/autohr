"use client";

import { apiClient } from "./client";

/**
 * 候选人详情 API client（任务 24）。
 *
 * 3 个新端点 + 复用 PATCH /api/screening/results/{id}/override（lib/api/screening.ts 集中）。
 */

// ============================================================================
// 类型（与 backend/app/schemas/candidate_detail.py 对应）
// ============================================================================

export interface CandidateSummary {
  id: string;
  name: string;
  phone: string | null;
  email: string | null;
  source_type: string | null;
  source_id: string | null;
  created_at: string;
}

export interface CandidateResumeOut {
  id: string;
  parsed_text: string | null;
  file_storage_key: string;
  mime_type: string | null;
  filename: string | null;
}

export interface ScreeningResultOut {
  id: string;
  job_id: string;
  candidate_id: string;
  disqualified: boolean;
  reasons: string[] | null;
  manually_overridden: boolean;
}

export interface ScoreOut {
  id: string;
  job_id: string;
  candidate_id: string;
  total: number;
  skill: number | null;
  experience: number | null;
  education: number | null;
  stability: number | null;
  potential: number | null;
  model_used: string | null;
  llm_call_id: string | null;
}

export interface WorkHistoryEntry {
  company: string | null;
  title: string | null;
  start_date: string | null;
  end_date: string | null;
  description: string | null;
}

export interface CandidateStructure {
  name: string | null;
  name_confidence: number;
  phone: string | null;
  phone_confidence: number;
  email: string | null;
  email_confidence: number;
  education: "high_school" | "bachelor" | "master" | "phd" | "other" | null;
  education_confidence: number;
  years_of_experience: number | null;
  years_of_experience_confidence: number;
  skills: string[];
  skills_confidence: number;
  expected_salary: string | null;
  expected_salary_confidence: number;
  current_company: string | null;
  current_company_confidence: number;
  work_history: WorkHistoryEntry[];
  work_history_confidence: number;
}

export interface CandidateDetailResponse {
  candidate: CandidateSummary;
  screening_result: ScreeningResultOut | null;
  score: ScoreOut | null;
  parsed_structure: CandidateStructure | null;
  resume: CandidateResumeOut | null;
}

export interface ResumeUrlResponse {
  url: string;
  expires_at: string;
  mime_type: string | null;
  filename: string | null;
}

export type ActivityType = "audit_log" | "override";

export interface CandidateActivityItem {
  type: ActivityType;
  id: string;
  created_at: string;
  actor_id: string | null;
  action: string;
  summary: string;
  details: Record<string, unknown> | null;
}

export interface CandidateActivityListResponse {
  items: CandidateActivityItem[];
  total: number;
  page: number;
  page_size: number;
}

// ============================================================================
// API
// ============================================================================

export async function getCandidateDetailApi(
  candidateId: string,
  jobId: string,
): Promise<CandidateDetailResponse> {
  const { data } = await apiClient.get<CandidateDetailResponse>(
    `/api/candidates/${candidateId}/detail`,
    { params: { job_id: jobId } },
  );
  return data;
}

export async function getCandidateResumeUrlApi(
  candidateId: string,
): Promise<ResumeUrlResponse> {
  const { data } = await apiClient.get<ResumeUrlResponse>(
    `/api/candidates/${candidateId}/resume-url`,
  );
  return data;
}

export async function listCandidateActivityApi(
  candidateId: string,
  params: { page?: number; page_size?: number } = {},
): Promise<CandidateActivityListResponse> {
  const { data } = await apiClient.get<CandidateActivityListResponse>(
    `/api/candidates/${candidateId}/activity`,
    { params },
  );
  return data;
}

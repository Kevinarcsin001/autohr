"use client";

import { apiClient } from "./client";

// ============================================================================
// 类型（与 backend/app/schemas/job.py 对应）
// ============================================================================

export type JobStatus = "draft" | "active" | "closed";
export type EducationLevel = "high_school" | "bachelor" | "master" | "phd";

export interface HardRequirements {
  min_education?: EducationLevel | null;
  min_years?: number | null;
  required_skills?: string[] | null;
  excluded_companies?: string[] | null;
}

export interface JobOut {
  id: string;
  team_id: string;
  title: string;
  jd_text: string;
  status: JobStatus;
  current_version: number;
  llm_config: Record<string, unknown> | null;
  hard_requirements: HardRequirements;
  created_by: string;
  created_at: string;
  updated_at: string;
}

export interface JobListItem {
  id: string;
  title: string;
  status: JobStatus;
  current_version: number;
  created_at: string;
  updated_at: string;
}

export interface JobListResponse {
  items: JobListItem[];
  page: number;
  page_size: number;
  total: number;
}

export interface JobVersionOut {
  id: string;
  job_id: string;
  version: number;
  snapshot: {
    title: string;
    jd_text: string;
    status: JobStatus;
    llm_config: Record<string, unknown> | null;
    hard_requirements: HardRequirements;
  };
  changed_by: string | null;
  changed_at: string;
}

export interface JobCreatePayload {
  title: string;
  jd_text: string;
  status?: JobStatus;
  hard_requirements?: HardRequirements;
  llm_config?: Record<string, unknown> | null;
}

export type JobUpdatePayload = Partial<JobCreatePayload>;

// ============================================================================
// API
// ============================================================================

export async function listJobsApi(params: {
  status?: JobStatus;
  page?: number;
  page_size?: number;
}): Promise<JobListResponse> {
  const { data } = await apiClient.get<JobListResponse>("/api/jobs/", {
    params,
  });
  return data;
}

export async function getJobApi(jobId: string): Promise<JobOut> {
  const { data } = await apiClient.get<JobOut>(`/api/jobs/${jobId}`);
  return data;
}

export async function createJobApi(payload: JobCreatePayload): Promise<JobOut> {
  const { data } = await apiClient.post<JobOut>("/api/jobs/", payload);
  return data;
}

export async function updateJobApi(
  jobId: string,
  payload: JobUpdatePayload,
): Promise<JobOut> {
  const { data } = await apiClient.patch<JobOut>(`/api/jobs/${jobId}`, payload);
  return data;
}

export async function deleteJobApi(jobId: string): Promise<void> {
  await apiClient.delete(`/api/jobs/${jobId}`);
}

export async function listJobVersionsApi(
  jobId: string,
): Promise<JobVersionOut[]> {
  const { data } = await apiClient.get<JobVersionOut[]>(
    `/api/jobs/${jobId}/versions`,
  );
  return data;
}

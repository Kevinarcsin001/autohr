"use client";

import { apiClient } from "./client";

// ============================================================================
// 类型（与 backend/app/schemas/candidate_list.py 对应）
// ============================================================================

// ============================================================================
// 团队级候选人列表
// ============================================================================

export interface TeamCandidateItem {
  id: string;
  name: string;
  email: string | null;
  phone: string | null;
  source_type: string | null;
  education: string | null;
  skills: string[];
  years_of_experience: number | null;
  created_at: string | null;
}

export interface TeamCandidateListResponse {
  items: TeamCandidateItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface TeamCandidateListParams {
  source?: string;
  education?: string;
  skill?: string;
  search?: string;
  sort_by?: string;
  sort_order?: string;
  page?: number;
  page_size?: number;
}

export async function listTeamCandidatesApi(
  params: TeamCandidateListParams,
): Promise<TeamCandidateListResponse> {
  const { data } = await apiClient.get<TeamCandidateListResponse>(
    "/api/candidates",
    { params },
  );
  return data;
}

// ============================================================================
// 职位级候选人列表
// ============================================================================

export type CandidateGroup =
  | "all"
  | "passed"
  | "disqualified"
  | "needs_review"
  | "pending";

export type SortBy =
  | "total"
  | "skill"
  | "experience"
  | "education"
  | "stability"
  | "potential"
  | "name";

export type SortOrder = "asc" | "desc";

export type EducationLevel =
  | "high_school"
  | "bachelor"
  | "master"
  | "phd"
  | "other";

export type CandidateSource = "upload" | "platform" | "email";

export interface CandidateListItem {
  id: string;
  name: string;
  email: string | null;
  phone: string | null;

  // 来源
  source_type: CandidateSource | null;
  source_id: string | null;

  // 筛选
  screening_id: string | null;
  disqualified: boolean | null;
  /** 三态筛选：字段缺失/无法判定 → 待复核（与 disqualified 互斥） */
  needs_review: boolean;
  screening_reasons: string[] | null;
  manually_overridden: boolean;

  // 评分
  score_id: string | null;
  total: number | null;
  skill: number | null;
  experience: number | null;
  education_score: number | null;
  stability: number | null;
  potential: number | null;
  model_used: string | null;

  // 结构化
  education: EducationLevel | string | null;
  years_of_experience: number | null;
  current_company: string | null;
  skills: string[];

  // 分组
  group: "passed" | "disqualified" | "pending";

  created_at: string;
  updated_at: string | null;
}

export interface CandidateListResponse {
  items: CandidateListItem[];
  total: number;
  page: number;
  page_size: number;
  group_counts: {
    passed: number;
    disqualified: number;
    needs_review: number;
    pending: number;
  };
}

export interface CandidateListParams {
  group?: CandidateGroup;
  min_score?: number;
  max_score?: number;
  education?: EducationLevel;
  min_years?: number;
  max_years?: number;
  skill?: string;
  source?: CandidateSource;
  sort_by?: SortBy;
  sort_order?: SortOrder;
  page?: number;
  page_size?: number;
}

// ============================================================================
// API
// ============================================================================

export async function listJobCandidatesApi(
  jobId: string,
  params: CandidateListParams
): Promise<CandidateListResponse> {
  const { data } = await apiClient.get<CandidateListResponse>(
    `/api/jobs/${jobId}/candidates`,
    { params }
  );
  return data;
}

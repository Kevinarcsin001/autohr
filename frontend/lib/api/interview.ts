"use client";

import { apiClient } from "./client";

/**
 * 面试 API client：问题 + 反馈 + 会话管理 + 录用建议。
 */

// ============================================================================
// 面试问题
// ============================================================================

export type InterviewDimension = "skill" | "project" | "weakness" | "culture";

export interface InterviewQuestionOut {
  id: string;
  session_id?: string | null;
  candidate_id: string;
  job_id: string;
  batch_id: string;
  dimension: InterviewDimension;
  question: string;
  sort_order: number;
  generated_by: string | null;
  bank_question_id?: string | null;
  /** 非空 → 来自题库；空 → AI 现场生成。 */
  feedback_id?: string | null;
  feedback?: string | null;
  rating?: number | null;
}

export interface InterviewQuestionListResponse {
  items: InterviewQuestionOut[];
  total: number;
}

export interface FeedbackRequest {
  feedback?: string | null;
  rating?: number | null;
}

export interface FeedbackOut {
  id: string;
  question_id: string;
  reviewer_id: string;
  feedback: string | null;
  rating: number | null;
}

export interface FeedbackResponse {
  feedback: FeedbackOut;
  question: InterviewQuestionOut;
}

export async function listInterviewQuestionsApi(
  candidateId: string,
  jobId: string,
): Promise<InterviewQuestionListResponse> {
  const { data } = await apiClient.get<InterviewQuestionListResponse>(
    "/api/interview/questions",
    { params: { candidate_id: candidateId, job_id: jobId } },
  );
  return data;
}

export async function submitFeedbackApi(
  questionId: string,
  payload: FeedbackRequest,
): Promise<FeedbackResponse> {
  const { data } = await apiClient.post<FeedbackResponse>(
    `/api/interview/questions/${questionId}/feedback`,
    payload,
  );
  return data;
}

// ============================================================================
// 面试会话
// ============================================================================

export type InterviewSessionStatus = "scheduled" | "in_progress" | "completed";

export interface InterviewSessionOut {
  id: string;
  candidate_id: string;
  job_id: string;
  status: InterviewSessionStatus;
  interviewer_id: string | null;
  overall_notes: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface InterviewSessionListItem {
  id: string;
  candidate_id: string;
  candidate_name: string | null;
  job_id: string;
  job_title: string | null;
  status: InterviewSessionStatus;
  interviewer_id: string | null;
  interviewer_name: string | null;
  question_count: number;
  created_at: string | null;
}

export interface InterviewSessionListResponse {
  items: InterviewSessionListItem[];
  total: number;
}

export interface SessionDetailResponse {
  session: InterviewSessionOut;
  candidate_name: string | null;
  candidate_email: string | null;
  job_title: string | null;
  job_jd_summary: string | null;
  questions: InterviewQuestionOut[];
  recommendation: HiringRecommendationOut | null;
}

export interface CreateSessionRequest {
  candidate_id: string;
  job_id: string;
}

export interface UpdateSessionRequest {
  status?: InterviewSessionStatus;
  interviewer_id?: string;
  overall_notes?: string;
}

export interface BatchFeedbackItem {
  question_id: string;
  feedback?: string | null;
  rating?: number | null;
}

export interface BatchFeedbackRequest {
  feedbacks: BatchFeedbackItem[];
}

export interface BatchFeedbackResponse {
  saved: number;
  errors: { question_id: string; error: string }[];
}

export async function createInterviewSessionApi(
  payload: CreateSessionRequest,
): Promise<InterviewSessionOut> {
  const { data } = await apiClient.post<InterviewSessionOut>(
    "/api/interview/sessions",
    payload,
  );
  return data;
}

export async function listInterviewSessionsApi(
  params: {
    status?: string;
    job_id?: string;
    page?: number;
    page_size?: number;
  },
): Promise<InterviewSessionListResponse> {
  const { data } = await apiClient.get<InterviewSessionListResponse>(
    "/api/interview/sessions",
    { params },
  );
  return data;
}

export async function getInterviewSessionApi(
  sessionId: string,
): Promise<SessionDetailResponse> {
  const { data } = await apiClient.get<SessionDetailResponse>(
    `/api/interview/sessions/${sessionId}`,
  );
  return data;
}

export async function updateInterviewSessionApi(
  sessionId: string,
  payload: UpdateSessionRequest,
): Promise<InterviewSessionOut> {
  const { data } = await apiClient.patch<InterviewSessionOut>(
    `/api/interview/sessions/${sessionId}`,
    payload,
  );
  return data;
}

export async function batchSaveFeedbackApi(
  sessionId: string,
  payload: BatchFeedbackRequest,
): Promise<BatchFeedbackResponse> {
  const { data } = await apiClient.post<BatchFeedbackResponse>(
    `/api/interview/sessions/${sessionId}/feedback`,
    payload,
  );
  return data;
}

// ============================================================================
// 录用建议
// ============================================================================

export type HiringRecommendation = "hire" | "reserve" | "reject";

export interface HiringRecommendationOut {
  id: string;
  session_id: string;
  recommendation: HiringRecommendation;
  reasons: string[] | null;
  risks: string[] | null;
  probation_focus: string[] | null;
  generated_by: string | null;
  created_at: string | null;
}

export interface GenerateRecommendationResponse {
  recommendation: HiringRecommendationOut;
}

export async function generateRecommendationApi(
  sessionId: string,
): Promise<GenerateRecommendationResponse> {
  const { data } = await apiClient.post<GenerateRecommendationResponse>(
    `/api/interview/sessions/${sessionId}/recommend`,
    {},
  );
  return data;
}

export async function getRecommendationApi(
  sessionId: string,
): Promise<GenerateRecommendationResponse> {
  const { data } = await apiClient.get<GenerateRecommendationResponse>(
    `/api/interview/sessions/${sessionId}/recommend`,
  );
  return data;
}

"use client";

import { apiClient } from "./client";

/**
 * 面试问题 API client（任务 24 复用）：列出最新 batch + 写反馈。
 */

export type InterviewDimension = "skill" | "project" | "weakness" | "culture";

export interface InterviewQuestionOut {
  id: string;
  candidate_id: string;
  job_id: string;
  batch_id: string;
  dimension: InterviewDimension;
  question: string;
  sort_order: number;
  generated_by: string | null;
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

"use client";

import { apiClient } from "./client";

/**
 * 推荐理由 API client（任务 24 复用）：按 score_id 拉取。
 */

export type ReasonType = "recommend" | "disqualify";

export interface ReasonOut {
  id: string;
  score_id: string;
  type: ReasonType;
  bullet_points: string[] | null;
  validated: boolean | null;
}

export interface ReasonListResponse {
  items: ReasonOut[];
  total: number;
}

export async function listReasonsByScoreApi(
  scoreId: string,
): Promise<ReasonListResponse> {
  const { data } = await apiClient.get<ReasonListResponse>(
    `/api/reasons/by-score/${scoreId}`,
  );
  return data;
}

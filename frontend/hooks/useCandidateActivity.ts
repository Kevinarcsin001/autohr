"use client";

import { useQuery } from "@tanstack/react-query";

import { listCandidateActivityApi } from "@/lib/api/candidateDetail";

/**
 * 候选人活动时间线 hook（任务 24）。
 */

export const CANDIDATE_ACTIVITY_KEY = (
  candidateId: string,
  page: number,
  pageSize: number,
) => ["candidate-activity", candidateId, page, pageSize] as const;

export function useCandidateActivity(
  candidateId: string | undefined,
  page = 1,
  pageSize = 20,
) {
  return useQuery({
    queryKey: CANDIDATE_ACTIVITY_KEY(candidateId ?? "", page, pageSize),
    queryFn: () =>
      listCandidateActivityApi(candidateId as string, {
        page,
        page_size: pageSize,
      }),
    enabled: !!candidateId,
    staleTime: 10_000,
  });
}

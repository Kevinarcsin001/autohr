"use client";

import { useQuery } from "@tanstack/react-query";

import { getCandidateDetailApi } from "@/lib/api/candidateDetail";

/**
 * 候选人详情 hook（任务 24）。
 */

export const CANDIDATE_DETAIL_KEY = (candidateId: string, jobId?: string) =>
  jobId
    ? (["candidate-detail", candidateId, jobId] as const)
    : (["candidate-detail", candidateId] as const);

export function useCandidateDetail(
  candidateId: string | undefined,
  jobId?: string,
) {
  return useQuery({
    queryKey: CANDIDATE_DETAIL_KEY(candidateId ?? "", jobId),
    queryFn: () =>
      getCandidateDetailApi(candidateId as string, jobId),
    enabled: !!candidateId,
    staleTime: 30_000,
  });
}

"use client";

import { useQuery } from "@tanstack/react-query";

import { getCandidateDetailApi } from "@/lib/api/candidateDetail";

/**
 * 候选人详情 hook（任务 24）。
 */

export const CANDIDATE_DETAIL_KEY = (candidateId: string, jobId: string) =>
  ["candidate-detail", candidateId, jobId] as const;

export function useCandidateDetail(
  candidateId: string | undefined,
  jobId: string | undefined,
) {
  return useQuery({
    queryKey: CANDIDATE_DETAIL_KEY(candidateId ?? "", jobId ?? ""),
    queryFn: () =>
      getCandidateDetailApi(candidateId as string, jobId as string),
    enabled: !!candidateId && !!jobId,
    staleTime: 30_000,
  });
}

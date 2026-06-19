"use client";

import { useQuery } from "@tanstack/react-query";

import {
  listJobCandidatesApi,
  type CandidateListParams,
} from "@/lib/api/candidates";

// ============================================================================
// Query Keys
// ============================================================================

export const CANDIDATES_KEY = (
  jobId: string,
  params: CandidateListParams
) => ["candidates", "list", jobId, params] as const;

// ============================================================================
// Hooks
// ============================================================================

export function useCandidates(jobId: string, params: CandidateListParams) {
  return useQuery({
    queryKey: CANDIDATES_KEY(jobId, params),
    queryFn: () => listJobCandidatesApi(jobId, params),
    enabled: !!jobId,
    staleTime: 10_000,
    placeholderData: (prev) => prev, // 翻页时保留旧数据避免闪烁
  });
}

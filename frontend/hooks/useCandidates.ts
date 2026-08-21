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
    // 智能轮询：存在「已通过初筛但评分未落」的候选人（解析/评分异步中）时，
    // 5s 自动刷新直到全部完成；全部有评分则停止轮询
    refetchInterval: (query) => {
      const items = query.state.data?.items ?? [];
      const pending = items.some(
        (c: { disqualified: boolean | null; score_id: string | null }) =>
          c.disqualified === false && c.score_id === null,
      );
      return pending ? 5_000 : false;
    },
  });
}

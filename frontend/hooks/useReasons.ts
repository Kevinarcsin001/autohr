"use client";

import { useQuery } from "@tanstack/react-query";

import { listReasonsByScoreApi } from "@/lib/api/reasons";

/**
 * 推荐理由 hook（任务 24）。
 */

const REASONS_KEY = (scoreId: string) =>
  ["score-reasons", scoreId] as const;

export function useReasons(scoreId: string | undefined | null) {
  return useQuery({
    queryKey: REASONS_KEY(scoreId ?? ""),
    queryFn: () => listReasonsByScoreApi(scoreId as string),
    enabled: !!scoreId,
    staleTime: 60_000,
  });
}

"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";

import {
  overrideScreeningResultApi,
  type OverrideRequest,
} from "@/lib/api/screening";
import { CANDIDATE_DETAIL_KEY } from "./useCandidateDetail";
import { CANDIDATE_ACTIVITY_KEY } from "./useCandidateActivity";

/**
 * HR 改判 hook（任务 24）。
 *
 * 成功后失效 detail + activity（界面刷新）+ 列表页（candidate 列表可能受影响）。
 */
export function useOverrideCandidate(
  candidateId: string,
  jobId?: string,
  options?: {
    onSuccess?: () => void;
    onError?: (err: unknown) => void;
  },
) {
  const qc = useQueryClient();

  return useMutation({
    mutationFn: (params: {
      screeningResultId: string;
      payload: OverrideRequest;
    }) =>
      overrideScreeningResultApi(
        params.screeningResultId,
        params.payload,
      ),
    onSuccess: () => {
      qc.invalidateQueries({
        queryKey: CANDIDATE_DETAIL_KEY(candidateId, jobId),
      });
      qc.invalidateQueries({
        queryKey: ["candidate-activity", candidateId],
      });
      qc.invalidateQueries({
        queryKey: ["candidates", "list"],
      });
      options?.onSuccess?.();
    },
    onError: (err) => {
      options?.onError?.(err);
    },
  });
}

export { CANDIDATE_ACTIVITY_KEY };

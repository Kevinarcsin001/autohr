"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  CandidateJobOutcome,
  FinalStatus,
  getOutcomeApi,
  putOutcomeApi,
} from "@/lib/api/outcome";

/** 查询候选人 × 职位的最终用人结果。 */
export function useOutcome(jobId: string, candidateId: string) {
  return useQuery<CandidateJobOutcome | null>({
    queryKey: ["outcome", jobId, candidateId],
    queryFn: () => getOutcomeApi(jobId, candidateId),
    enabled: Boolean(jobId && candidateId),
  });
}

/** 录入 / 更新用人结果（成功后失效结果与漏斗缓存）。 */
export function useUpsertOutcome(
  jobId: string,
  candidateId: string,
  opts?: { onSuccess?: (data: CandidateJobOutcome) => void },
) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: { final_status: FinalStatus; note?: string | null }) =>
      putOutcomeApi(jobId, candidateId, payload),
    onSuccess: (data) => {
      queryClient.invalidateQueries({
        queryKey: ["outcome", jobId, candidateId],
      });
      queryClient.invalidateQueries({ queryKey: ["funnel"] });
      opts?.onSuccess?.(data);
    },
  });
}

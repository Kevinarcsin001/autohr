"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createSessionApi,
  listInterviewSessionsApi,
  type InterviewSessionListResponse,
} from "@/lib/api/interview";

export const INTERVIEW_SESSIONS_KEY = (params: {
  status?: string;
  job_id?: string;
  page?: number;
  page_size?: number;
}) => ["interview-sessions", params] as const;

export function useInterviewSessions(params: {
  status?: string;
  job_id?: string;
  page?: number;
  page_size?: number;
}) {
  return useQuery<InterviewSessionListResponse>({
    queryKey: INTERVIEW_SESSIONS_KEY(params),
    queryFn: () => listInterviewSessionsApi(params),
    staleTime: 10_000,
    placeholderData: (prev) => prev,
  });
}

/** 新建面试会话 → 成功后失效列表。 */
export function useCreateInterviewSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: { candidate_id: string; job_id: string }) =>
      createSessionApi(payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["interview-sessions"] });
    },
  });
}

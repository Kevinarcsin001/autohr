"use client";

import { useQuery } from "@tanstack/react-query";

import {
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

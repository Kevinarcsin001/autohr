"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  getInterviewSessionApi,
  updateInterviewSessionApi,
  batchSaveFeedbackApi,
  type BatchFeedbackRequest,
  type SessionDetailResponse,
  type UpdateSessionRequest,
} from "@/lib/api/interview";
import { INTERVIEW_SESSIONS_KEY } from "./useInterviewSessions";

export const INTERVIEW_SESSION_KEY = (sessionId: string) =>
  ["interview-session", sessionId] as const;

export function useInterviewSession(sessionId: string | undefined) {
  return useQuery<SessionDetailResponse>({
    queryKey: INTERVIEW_SESSION_KEY(sessionId ?? ""),
    queryFn: () => getInterviewSessionApi(sessionId as string),
    enabled: !!sessionId,
    staleTime: 10_000,
  });
}

export function useUpdateInterviewSession(sessionId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: UpdateSessionRequest) =>
      updateInterviewSessionApi(sessionId, payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: INTERVIEW_SESSION_KEY(sessionId) });
      qc.invalidateQueries({ queryKey: ["interview-sessions"] });
    },
  });
}

export function useBatchSaveFeedback(sessionId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: BatchFeedbackRequest) =>
      batchSaveFeedbackApi(sessionId, payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: INTERVIEW_SESSION_KEY(sessionId) });
    },
  });
}

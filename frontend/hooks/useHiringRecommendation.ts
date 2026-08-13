"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  generateRecommendationApi,
  getRecommendationApi,
  type GenerateRecommendationResponse,
} from "@/lib/api/interview";
import { INTERVIEW_SESSION_KEY } from "./useInterviewSession";

const RECOMMENDATION_KEY = (sessionId: string) =>
  ["hiring-recommendation", sessionId] as const;

export function useHiringRecommendation(sessionId: string | undefined) {
  return useQuery<GenerateRecommendationResponse>({
    queryKey: RECOMMENDATION_KEY(sessionId ?? ""),
    queryFn: () => getRecommendationApi(sessionId as string),
    enabled: !!sessionId,
    staleTime: 60_000,
    retry: false,
  });
}

export function useGenerateRecommendation(sessionId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => generateRecommendationApi(sessionId),
    onSuccess: (data) => {
      qc.setQueryData(RECOMMENDATION_KEY(sessionId), data);
      qc.invalidateQueries({ queryKey: INTERVIEW_SESSION_KEY(sessionId) });
    },
  });
}

"use client";

import { useQuery } from "@tanstack/react-query";

import { listInterviewQuestionsApi } from "@/lib/api/interview";

/**
 * 面试问题列表 hook（任务 24）。
 */

const INTERVIEW_KEY = (candidateId: string, jobId: string) =>
  ["interview-questions", candidateId, jobId] as const;

export function useInterviewQuestions(
  candidateId: string | undefined,
  jobId: string | undefined,
) {
  return useQuery({
    queryKey: INTERVIEW_KEY(candidateId ?? "", jobId ?? ""),
    queryFn: () =>
      listInterviewQuestionsApi(candidateId as string, jobId as string),
    enabled: !!candidateId && !!jobId,
    staleTime: 30_000,
  });
}

export const INTERVIEW_QUESTIONS_KEY = INTERVIEW_KEY;

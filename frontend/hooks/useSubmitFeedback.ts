"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";

import {
  submitFeedbackApi,
  type FeedbackRequest,
} from "@/lib/api/interview";

/**
 * 面试反馈提交 hook（任务 24）。
 *
 * 成功后失效 interview questions（让反馈字段刷新）。
 */
const INTERVIEW_KEY = (candidateId: string, jobId: string) =>
  ["interview-questions", candidateId, jobId] as const;

export function useSubmitFeedback(
  candidateId: string,
  jobId: string,
  options?: {
    onSuccess?: () => void;
    onError?: (err: unknown) => void;
  },
) {
  const qc = useQueryClient();

  return useMutation({
    mutationFn: (params: {
      questionId: string;
      payload: FeedbackRequest;
    }) => submitFeedbackApi(params.questionId, params.payload),
    onSuccess: () => {
      qc.invalidateQueries({
        queryKey: INTERVIEW_KEY(candidateId, jobId),
      });
      options?.onSuccess?.();
    },
    onError: (err) => {
      options?.onError?.(err);
    },
  });
}

export const INTERVIEW_QUESTIONS_KEY = INTERVIEW_KEY;

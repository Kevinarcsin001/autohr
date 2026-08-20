"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  adaptiveAnswerApi,
  adaptiveAudioApi,
  adaptiveMarkOffsetApi,
  adaptiveNextApi,
  adaptiveProcessRecordingApi,
  adaptiveStartApi,
  adaptiveStateApi,
  adaptiveUploadRecordingApi,
} from "@/lib/api/interview";

const ADAPTIVE_KEY = (sessionId: string) =>
  ["interview-adaptive", sessionId] as const;

/** 启动（幂等）。成功后 invalidate 状态查询。 */
export function useAdaptiveStart(sessionId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => adaptiveStartApi(sessionId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ADAPTIVE_KEY(sessionId) });
      qc.invalidateQueries({ queryKey: ["interview-session", sessionId] });
      qc.invalidateQueries({ queryKey: ["interview-sessions"] });
    },
  });
}

/** 大屏状态（轮询驱动：回答/下一题操作后自动刷新）。 */
export function useAdaptiveState(sessionId: string, enabled = true) {
  return useQuery({
    queryKey: ADAPTIVE_KEY(sessionId),
    queryFn: () => adaptiveStateApi(sessionId),
    enabled: !!sessionId && enabled,
    staleTime: 5_000,
    retry: 1,
  });
}

/** 提交回答。 */
export function useAdaptiveAnswer(sessionId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: { turn_id: string; answer_text: string }) =>
      adaptiveAnswerApi(sessionId, payload),
    onSuccess: () => qc.invalidateQueries({ queryKey: ADAPTIVE_KEY(sessionId) }),
  });
}

/** 上传音频（转写+自动评分在后台异步完成）。 */
export function useAdaptiveAudio(sessionId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: { turn_id: string; audio: Blob; filename?: string }) =>
      adaptiveAudioApi(sessionId, payload),
    onSuccess: () => {
      // 转写中 → 定时刷新直到 done/failed
      qc.invalidateQueries({ queryKey: ADAPTIVE_KEY(sessionId) });
    },
  });
}

/** 获取下一题（可选：指定分支 / 跳过当前题 — 面试官控制权）。 */
export function useAdaptiveNext(sessionId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (opts?: { forceCategoryId?: string; skipCurrent?: boolean }) =>
      adaptiveNextApi(sessionId, opts),
    onSuccess: () => qc.invalidateQueries({ queryKey: ADAPTIVE_KEY(sessionId) }),
  });
}

/** 录制回捞三件套（M2b）。 */
export function useRecordingReplay(sessionId: string) {
  const qc = useQueryClient();
  const upload = useMutation({
    mutationFn: (file: File) => adaptiveUploadRecordingApi(sessionId, file),
    onSuccess: () => qc.invalidateQueries({ queryKey: ADAPTIVE_KEY(sessionId) }),
  });
  const process = useMutation({
    mutationFn: () => adaptiveProcessRecordingApi(sessionId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ADAPTIVE_KEY(sessionId) }),
  });
  const markOffset = useMutation({
    mutationFn: ({ turnId, offset }: { turnId: string; offset: string | number }) =>
      adaptiveMarkOffsetApi(sessionId, turnId, offset),
    onSuccess: () => qc.invalidateQueries({ queryKey: ADAPTIVE_KEY(sessionId) }),
  });
  return { upload, process, markOffset };
}

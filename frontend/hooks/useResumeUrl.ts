"use client";

import { useEffect, useState } from "react";

import { useQuery, useQueryClient } from "@tanstack/react-query";

import { getCandidateResumeUrlApi } from "@/lib/api/candidateDetail";

/**
 * 简历签名 URL hook（任务 24）。
 *
 * 策略（plan）：
 * - signed_url 5min 过期；前端在 expires_at 前 30s 自动 prefetch 新 URL
 * - prefetch 通过 invalidateQueries 触发重新拉取
 */

const RESUME_URL_KEY = (candidateId: string) =>
  ["candidate-resume-url", candidateId] as const;

// 默认提前 30s 刷新
const PREFETCH_LEAD_SECONDS = 30;

export function useResumeUrl(candidateId: string | undefined) {
  const qc = useQueryClient();
  const [expiresAt, setExpiresAt] = useState<string | null>(null);

  const query = useQuery({
    queryKey: RESUME_URL_KEY(candidateId ?? ""),
    queryFn: async () => {
      const data = await getCandidateResumeUrlApi(candidateId as string);
      setExpiresAt(data.expires_at);
      return data;
    },
    enabled: !!candidateId,
    staleTime: 60_000, // 1min
  });

  // 提前 30s 自动 invalidate（避免用户阅读中途失效）
  useEffect(() => {
    if (!expiresAt) return;
    const ms =
      new Date(expiresAt).getTime() - Date.now() - PREFETCH_LEAD_SECONDS * 1000;
    if (ms <= 0) {
      qc.invalidateQueries({
        queryKey: RESUME_URL_KEY(candidateId ?? ""),
      });
      return;
    }
    const timer = setTimeout(() => {
      qc.invalidateQueries({
        queryKey: RESUME_URL_KEY(candidateId ?? ""),
      });
    }, ms);
    return () => clearTimeout(timer);
  }, [expiresAt, candidateId, qc]);

  return query;
}

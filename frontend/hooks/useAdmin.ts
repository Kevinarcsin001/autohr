"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  deleteLLMConfigApi,
  getStatsApi,
  listLLMConfigsApi,
  upsertLLMConfigApi,
  type LLMConfigUpsertPayload,
  type StatsRange,
} from "@/lib/api/admin";

const LLM_KEY = ["admin", "llm-configs"] as const;
const statsKey = (range: StatsRange) => ["admin", "stats", range] as const;

// ============================================================================
// LLM 配置
// ============================================================================

export function useLLMConfigs() {
  return useQuery({
    queryKey: LLM_KEY,
    queryFn: () => listLLMConfigsApi(),
    staleTime: 30_000,
  });
}

export function useUpsertLLMConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: LLMConfigUpsertPayload) => upsertLLMConfigApi(payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: LLM_KEY });
    },
  });
}

export function useDeleteLLMConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (configId: string) => deleteLLMConfigApi(configId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: LLM_KEY });
    },
  });
}

// ============================================================================
// 统计
// ============================================================================

export function useAdminStats(range: StatsRange, refreshMs = 60_000) {
  return useQuery({
    queryKey: statsKey(range),
    queryFn: () => getStatsApi(range),
    refetchInterval: refreshMs,
    staleTime: 10_000,
  });
}

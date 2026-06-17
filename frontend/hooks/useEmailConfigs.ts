"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createEmailConfig,
  deleteEmailConfig,
  getEmailConfig,
  getEmailConfigStatus,
  updateEmailConfig,
  type EmailConfigCreateInput,
  type EmailConfigUpdateInput,
} from "@/lib/api/emailConfigs";

const CONFIG_KEY = ["email-config", "me"] as const;
const STATUS_KEY = ["email-config", "status"] as const;

export function useEmailConfig() {
  return useQuery({
    queryKey: CONFIG_KEY,
    queryFn: () => getEmailConfig(),
    staleTime: 15_000,
  });
}

export function useEmailConfigStatus(refreshMs = 30_000) {
  return useQuery({
    queryKey: STATUS_KEY,
    queryFn: () => getEmailConfigStatus(),
    refetchInterval: refreshMs,
    staleTime: 5_000,
  });
}

export function useCreateEmailConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: EmailConfigCreateInput) => createEmailConfig(payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: CONFIG_KEY });
      qc.invalidateQueries({ queryKey: STATUS_KEY });
    },
  });
}

export function useUpdateEmailConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: EmailConfigUpdateInput) => updateEmailConfig(payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: CONFIG_KEY });
      qc.invalidateQueries({ queryKey: STATUS_KEY });
    },
  });
}

export function useDeleteEmailConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => deleteEmailConfig(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: CONFIG_KEY });
      qc.invalidateQueries({ queryKey: STATUS_KEY });
    },
  });
}

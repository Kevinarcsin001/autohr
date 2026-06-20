"use client";

import { useQuery } from "@tanstack/react-query";
import { fetchDashboardStats } from "@/lib/api/dashboard";

import { useAuthStore } from "@/stores/authStore";

export function useDashboardStats() {
  const user = useAuthStore((s) => s.user);
  return useQuery({
    queryKey: ["dashboard-stats"],
    queryFn: fetchDashboardStats,
    staleTime: 15_000,
    refetchInterval: 30_000,
    enabled: !!user,
  });
}

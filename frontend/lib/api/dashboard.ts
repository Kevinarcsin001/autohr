import { apiClient } from "./client";

export interface DashboardStats {
  total_candidates: number;
  pending_candidates: number;
  passed_candidates: number;
  disqualified_candidates: number;
  active_jobs: number;
  total_jobs: number;
  pending_reviews: number;
}

export async function fetchDashboardStats(): Promise<DashboardStats> {
  const resp = await apiClient.get<DashboardStats>("/api/dashboard/stats");
  return resp.data;
}

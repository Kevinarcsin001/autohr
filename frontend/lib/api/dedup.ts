import { apiClient } from "./client";

export interface DedupMatchItem {
  id: string;
  candidate_a: string;
  candidate_b: string;
  name_a: string | null;
  name_b: string | null;
  similarity: Record<string, unknown>;
  status: "pending" | "merged" | "rejected";
}

export interface DedupMatchListResponse {
  items: DedupMatchItem[];
  total: number;
}

export async function fetchDedupMatches(): Promise<DedupMatchListResponse> {
  const resp = await apiClient.get<DedupMatchListResponse>(
    "/api/candidates/dedup-matches",
  );
  return resp.data;
}

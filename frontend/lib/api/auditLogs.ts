import { apiClient } from "./client";

export interface AuditLogItem {
  id: string;
  actor_id: string | null;
  action: string;
  target_type: string | null;
  target_id: string | null;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  ip: string | null;
  user_agent: string | null;
  created_at: string;
}

export interface AuditLogListResponse {
  items: AuditLogItem[];
  total: number;
  page: number;
  page_size: number;
}

export async function fetchAuditLogs(
  page = 1,
  pageSize = 50,
): Promise<AuditLogListResponse> {
  const resp = await apiClient.get<AuditLogListResponse>(
    `/api/audit-logs/?page=${page}&page_size=${pageSize}`,
  );
  return resp.data;
}

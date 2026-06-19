"use client";

import { apiClient } from "./client";

// ============================================================================
// 类型（与 backend/app/schemas/export.py 对应）
// ============================================================================

export type ExportFormat = "xlsx" | "csv";

export interface ExportFilters {
  /** 仅看已淘汰（true）或仅看未淘汰（false） */
  disqualified?: boolean;
  /** 总分下限 */
  min_score?: number;
  /** 多 team 聚合（admin 用，普通用户忽略） */
  team_ids?: string[];
}

export interface ExportRequestPayload {
  job_id: string;
  format?: ExportFormat;
  filters?: ExportFilters;
}

export interface ExportSyncResponse {
  mode: "sync";
  download_url: string;
  expires_in: number;
  row_count: number;
  file_key: string;
  file_size: number;
}

export interface ExportAsyncResponse {
  mode: "async";
  job_id: string;
  row_count: number;
}

export type ExportRequestResponse = ExportSyncResponse | ExportAsyncResponse;

export interface ExportResultQuery {
  job_id: string;
  status: "queued" | "running" | "success" | "failed" | "retry";
  file_key: string | null;
  file_size: number | null;
  row_count: number | null;
  download_url: string | null;
  error: string | null;
}

// ============================================================================
// API
// ============================================================================

export async function requestExportApi(
  payload: ExportRequestPayload
): Promise<ExportRequestResponse> {
  const { data } = await apiClient.post<ExportRequestResponse>(
    "/api/exports/",
    payload
  );
  return data;
}

export async function getExportStatusApi(
  jobId: string
): Promise<ExportResultQuery> {
  const { data } = await apiClient.get<ExportResultQuery>(
    `/api/exports/jobs/${jobId}`
  );
  return data;
}

export async function getDownloadUrlApi(fileKey: string): Promise<{
  download_url: string;
  expires_in: number;
}> {
  const { data } = await apiClient.get<{ download_url: string; expires_in: number }>(
    "/api/exports/download",
    { params: { file_key: fileKey } }
  );
  return data;
}

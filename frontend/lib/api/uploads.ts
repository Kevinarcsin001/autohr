"use client";

import { apiClient } from "./client";

// ============================================================================
// 类型（与 backend/app/schemas/upload.py 对应）
// ============================================================================

export interface UploadIntentItemInput {
  filename: string;
  size_bytes: number;
  mime_client: string;
}

export type UploadRejectReason =
  | "size_exceeded"
  | "extension_not_allowed"
  | "batch_too_large";

export type UploadConfirmRejectReason =
  | "object_missing"
  | "mime_not_allowed"
  | "mime_mismatch"
  | "cross_team"
  | "duplicate_enqueue";

export interface UploadIntentResponseItem {
  upload_id: string;
  filename: string;
  file_key: string;
  signed_url: string | null;
  expires_in: number | null;
  method: "PUT";
  status: "ok" | "rejected";
  reject_reason: UploadRejectReason | null;
}

export interface UploadIntentResponse {
  items: UploadIntentResponseItem[];
  accepted: number;
  rejected: number;
}

export interface UploadConfirmItemInput {
  upload_id: string;
  file_key: string;
}

export interface UploadConfirmResponseItem {
  upload_id: string;
  resume_id: string | null;
  candidate_id: string | null;
  status: "ok" | "rejected";
  reject_reason: UploadConfirmRejectReason | null;
}

export interface UploadConfirmResponse {
  items: UploadConfirmResponseItem[];
  confirmed: number;
  rejected: number;
}

// ============================================================================
// API
// ============================================================================

export async function createUploadIntent(
  files: UploadIntentItemInput[]
): Promise<UploadIntentResponse> {
  const resp = await apiClient.post<UploadIntentResponse>(
    "/api/uploads/intent",
    { files }
  );
  return resp.data;
}

export async function confirmUploads(
  items: UploadConfirmItemInput[],
  jobId?: string | null,
): Promise<UploadConfirmResponse> {
  const resp = await apiClient.post<UploadConfirmResponse>(
    "/api/uploads/confirm",
    { items, ...(jobId ? { job_id: jobId } : {}) }
  );
  return resp.data;
}

/**
 * 直传文件到签名 URL（PUT，不带 Authorization header —— MinIO 用 URL 自身的签名鉴权）。
 * onProgress 可选用于上传进度展示。
 */
export async function putFileToSignedUrl(
  signedUrl: string,
  file: File | Blob,
  mime: string,
  onProgress?: (loaded: number, total: number) => void
): Promise<void> {
  const { default: axios } = await import("axios");
  await axios.put(signedUrl, file, {
    headers: {
      "Content-Type": mime,
    },
    onUploadProgress: (e) => {
      if (onProgress && e.total != null) {
        onProgress(e.loaded, e.total);
      }
    },
  });
}

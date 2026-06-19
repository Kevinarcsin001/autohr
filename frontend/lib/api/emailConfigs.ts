"use client";

import { apiClient } from "./client";

// ============================================================================
// 类型（与 backend/app/schemas/email.py 对应）
// ============================================================================

export type AlertLevel = "none" | "warning" | "critical";

export interface EmailConfigCreateInput {
  imap_host: string;
  imap_port: number;
  username: string;
  password: string;
  poll_interval_min: number;
  enabled: boolean;
}

export interface EmailConfigUpdateInput {
  imap_host?: string;
  imap_port?: number;
  username?: string;
  password?: string;
  poll_interval_min?: number;
  enabled?: boolean;
  clear_alert?: boolean;
}

export interface EmailConfigOut {
  id: string;
  team_id: string;
  imap_host: string;
  imap_port: number;
  username: string;
  poll_interval_min: number;
  enabled: boolean;
  last_fetched_at: string | null;
  consecutive_failures: number;
  paused_until: string | null;
  last_error_summary: string | null;
  alert_level: AlertLevel;
  created_at: string;
  updated_at: string;
}

export interface EmailConfigStatus {
  configured: boolean;
  enabled: boolean;
  is_paused: boolean;
  paused_until: string | null;
  consecutive_failures: number;
  alert_level: AlertLevel;
  last_fetched_at: string | null;
  last_error_summary: string | null;
  next_scheduled_in_seconds: number | null;
}

// ============================================================================
// API
// ============================================================================

export async function getEmailConfig(): Promise<EmailConfigOut | null> {
  const resp = await apiClient.get<EmailConfigOut | null>("/api/email-configs/");
  return resp.data;
}

export async function getEmailConfigStatus(): Promise<EmailConfigStatus> {
  const resp = await apiClient.get<EmailConfigStatus>("/api/email-configs/status");
  return resp.data;
}

export async function createEmailConfig(
  payload: EmailConfigCreateInput
): Promise<EmailConfigOut> {
  const resp = await apiClient.post<EmailConfigOut>(
    "/api/email-configs/",
    payload
  );
  return resp.data;
}

export async function updateEmailConfig(
  payload: EmailConfigUpdateInput
): Promise<EmailConfigOut> {
  const resp = await apiClient.patch<EmailConfigOut>(
    "/api/email-configs/",
    payload
  );
  return resp.data;
}

export async function deleteEmailConfig(): Promise<void> {
  await apiClient.delete("/api/email-configs/");
}

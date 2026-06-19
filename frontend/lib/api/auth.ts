"use client";

import { apiClient } from "./client";

// ============================================================================
// 类型（与 backend/app/schemas/auth.py 对应）
// ============================================================================

export interface UserOut {
  id: string;
  email: string;
  name: string;
  role: "admin" | "member";
  team_id: string | null;
}

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

export interface AuthResponse {
  user: UserOut;
  tokens: TokenPair;
}

export interface RegisterPayload {
  email: string;
  password: string;
  name: string;
}

export interface LoginPayload {
  email: string;
  password: string;
}

export interface InvitePayload {
  email: string;
  role: "admin" | "member";
  name?: string;
}

export interface InviteOut {
  id: string;
  email: string;
  role: "admin" | "member";
  invite_token: string;
  expires_at: string;
}

export interface AcceptInvitePayload {
  invite_token: string;
  name: string;
  password: string;
}

// ============================================================================
// API 调用
// ============================================================================

export async function registerApi(payload: RegisterPayload): Promise<AuthResponse> {
  const { data } = await apiClient.post<AuthResponse>("/api/auth/register", payload);
  return data;
}

export async function loginApi(payload: LoginPayload): Promise<AuthResponse> {
  const { data } = await apiClient.post<AuthResponse>("/api/auth/login", payload);
  return data;
}

export async function refreshApi(): Promise<{ access_token: string }> {
  const { data } = await apiClient.post<{ access_token: string; token_type: string }>(
    "/api/auth/refresh",
  );
  return data;
}

export async function logoutApi(): Promise<void> {
  await apiClient.post("/api/auth/logout");
}

export async function getMeApi(): Promise<UserOut> {
  const { data } = await apiClient.get<UserOut>("/api/auth/me");
  return data;
}

export async function inviteMemberApi(payload: InvitePayload): Promise<InviteOut> {
  const { data } = await apiClient.post<InviteOut>("/api/auth/invite", payload);
  return data;
}

export async function listInvitesApi(): Promise<InviteOut[]> {
  const { data } = await apiClient.get<InviteOut[]>("/api/auth/invites");
  return data;
}

export async function acceptInviteApi(payload: AcceptInvitePayload): Promise<AuthResponse> {
  const { data } = await apiClient.post<AuthResponse>("/api/auth/accept-invite", payload);
  return data;
}

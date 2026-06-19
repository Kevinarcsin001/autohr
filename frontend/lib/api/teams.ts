"use client";

import { apiClient } from "./client";
import type { InviteOut } from "./auth";

// ============================================================================
// 类型（与 backend/app/schemas/team.py 对应）
// ============================================================================

export interface TeamOut {
  id: string;
  name: string;
}

export interface TeamMemberOut {
  id: string;
  email: string;
  name: string;
  role: "admin" | "member";
  created_at: string;
}

export interface TeamDetailOut {
  team: TeamOut;
  members: TeamMemberOut[];
}

export interface CreateInvitePayload {
  email: string;
  role: "admin" | "member";
  name?: string;
}

export interface UpdateRolePayload {
  role: "admin" | "member";
}

// ============================================================================
// API
// ============================================================================

export async function getMyTeamApi(): Promise<TeamDetailOut> {
  const { data } = await apiClient.get<TeamDetailOut>("/api/teams/me");
  return data;
}

export async function listMembersApi(teamId: string): Promise<TeamMemberOut[]> {
  const { data } = await apiClient.get<TeamMemberOut[]>(
    `/api/teams/${teamId}/members`,
  );
  return data;
}

export async function inviteTeamMemberApi(
  teamId: string,
  payload: CreateInvitePayload,
): Promise<InviteOut> {
  const { data } = await apiClient.post<InviteOut>(
    `/api/teams/${teamId}/invite`,
    payload,
  );
  return data;
}

export async function updateMemberRoleApi(
  teamId: string,
  userId: string,
  payload: UpdateRolePayload,
): Promise<TeamMemberOut> {
  const { data } = await apiClient.patch<TeamMemberOut>(
    `/api/teams/${teamId}/members/${userId}/role`,
    payload,
  );
  return data;
}

export async function removeMemberApi(
  teamId: string,
  userId: string,
): Promise<void> {
  await apiClient.delete(`/api/teams/${teamId}/members/${userId}`);
}

export async function listTeamInvitesApi(teamId: string): Promise<InviteOut[]> {
  const { data } = await apiClient.get<InviteOut[]>(
    `/api/teams/${teamId}/invites`,
  );
  return data;
}

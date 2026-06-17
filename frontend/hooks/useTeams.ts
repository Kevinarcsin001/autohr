"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  getMyTeamApi,
  inviteTeamMemberApi,
  listTeamInvitesApi,
  removeMemberApi,
  updateMemberRoleApi,
  type CreateInvitePayload,
  type UpdateRolePayload,
} from "@/lib/api/teams";

const TEAM_KEY = ["team", "me"] as const;
const INVITES_KEY = (teamId: string) => ["team", teamId, "invites"] as const;

export function useMyTeam() {
  return useQuery({
    queryKey: TEAM_KEY,
    queryFn: () => getMyTeamApi(),
    staleTime: 30_000,
  });
}

export function useTeamInvites(teamId: string | undefined) {
  return useQuery({
    queryKey: INVITES_KEY(teamId ?? ""),
    queryFn: () => listTeamInvitesApi(teamId!),
    enabled: !!teamId,
    staleTime: 30_000,
  });
}

export function useInviteTeamMember(teamId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: CreateInvitePayload) =>
      inviteTeamMemberApi(teamId!, payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: INVITES_KEY(teamId!) });
    },
  });
}

export function useUpdateMemberRole(teamId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      userId,
      payload,
    }: {
      userId: string;
      payload: UpdateRolePayload;
    }) => updateMemberRoleApi(teamId!, userId, payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: TEAM_KEY });
    },
  });
}

export function useRemoveMember(teamId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (userId: string) => removeMemberApi(teamId!, userId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: TEAM_KEY });
    },
  });
}

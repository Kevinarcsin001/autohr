"use client";

import { useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import {
  acceptInviteApi,
  getMeApi,
  inviteMemberApi,
  listInvitesApi,
  loginApi,
  logoutApi,
  refreshApi,
  registerApi,
  type AcceptInvitePayload,
  type InvitePayload,
  type LoginPayload,
  type RegisterPayload,
} from "@/lib/api/auth";
import { useAuthStore } from "@/stores/authStore";

// ============================================================================
// 启动时刷新会话（在 AuthProvider 内调用）
// ============================================================================

export function useBootstrapSession() {
  const setSession = useAuthStore((s) => s.setSession);
  const setAccessToken = useAuthStore((s) => s.setAccessToken);
  const setStatus = useAuthStore((s) => s.setStatus);
  const accessToken = useAuthStore((s) => s.accessToken);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      // 没有 access token？尝试用 refresh cookie 换一个
      try {
        let token = accessToken;
        if (!token) {
          const { access_token } = await refreshApi();
          token = access_token;
          setAccessToken(access_token);
        }
        const me = await getMeApi();
        if (!cancelled) setSession(me, token!);
      } catch {
        if (!cancelled) setStatus("guest");
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
}

// ============================================================================
// 注册 / 登录 / 接受邀请 mutations
// ============================================================================

export function useRegister() {
  const setSession = useAuthStore((s) => s.setSession);
  const qc = useQueryClient();
  const router = useRouter();

  return useMutation({
    mutationFn: (payload: RegisterPayload) => registerApi(payload),
    onSuccess: (data) => {
      setSession(data.user, data.tokens.access_token);
      qc.setQueryData(["me"], data.user);
      router.push("/dashboard");
    },
  });
}

export function useLogin() {
  const setSession = useAuthStore((s) => s.setSession);
  const qc = useQueryClient();
  const router = useRouter();

  return useMutation({
    mutationFn: (payload: LoginPayload) => loginApi(payload),
    onSuccess: (data) => {
      setSession(data.user, data.tokens.access_token);
      qc.setQueryData(["me"], data.user);
      router.push("/dashboard");
    },
  });
}

export function useAcceptInvite() {
  const setSession = useAuthStore((s) => s.setSession);
  const qc = useQueryClient();
  const router = useRouter();

  return useMutation({
    mutationFn: (payload: AcceptInvitePayload) => acceptInviteApi(payload),
    onSuccess: (data) => {
      setSession(data.user, data.tokens.access_token);
      qc.setQueryData(["me"], data.user);
      router.push("/dashboard");
    },
  });
}

// ============================================================================
// 登出
// ============================================================================

export function useLogout() {
  const logout = useAuthStore((s) => s.logout);
  const qc = useQueryClient();
  const router = useRouter();

  return useMutation({
    mutationFn: () => logoutApi(),
    onSettled: () => {
      logout();
      qc.clear();
      router.push("/login");
    },
  });
}

// ============================================================================
// 邀请管理（admin）
// ============================================================================

export function useInviteMember() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: InvitePayload) => inviteMemberApi(payload),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["invites"] }),
  });
}

export function useInvites() {
  return useQuery({
    queryKey: ["invites"],
    queryFn: () => listInvitesApi(),
    staleTime: 30_000,
  });
}

// ============================================================================
// 当前用户
// ============================================================================

export function useMe() {
  return useQuery({
    queryKey: ["me"],
    queryFn: () => getMeApi(),
    enabled: false, // 主要靠 authStore；此处仅在有 token 时按需触发
  });
}

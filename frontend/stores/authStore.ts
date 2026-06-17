"use client";

import { create } from "zustand";
import type { UserOut } from "@/lib/api/auth";

/**
 * 认证状态 store（任务 5）。
 *
 * - access_token **仅存内存**：避免 XSS 窃取；刷新页面后通过 refresh cookie 重新获取
 * - user：当前登录用户对象；登录/注册/接受邀请成功时写入
 * - status：用于 SSR 友好的 loading 判断（"loading" | "authenticated" | "guest"）
 *
 * refresh token 走 httpOnly cookie（后端 Set-Cookie），前端无法读取。
 */
interface AuthState {
  user: UserOut | null;
  accessToken: string | null;
  status: "loading" | "authenticated" | "guest";

  // actions
  setSession: (user: UserOut, accessToken: string) => void;
  setAccessToken: (token: string) => void;
  setStatus: (status: AuthState["status"]) => void;
  logout: () => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  accessToken: null,
  status: "loading",

  setSession: (user, accessToken) =>
    set({ user, accessToken, status: "authenticated" }),

  setAccessToken: (token: string) => set({ accessToken: token }),

  setStatus: (status) => set({ status }),

  logout: () => set({ user: null, accessToken: null, status: "guest" }),
}));

// 选择器（避免不必要重渲染）
export const selectAccessToken = (s: AuthState) => s.accessToken;
export const selectUser = (s: AuthState) => s.user;
export const selectStatus = (s: AuthState) => s.status;

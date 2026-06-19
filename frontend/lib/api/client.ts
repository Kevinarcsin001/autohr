"use client";

import axios, { AxiosError, type AxiosInstance, type InternalAxiosRequestConfig } from "axios";

/**
 * 后端 API 客户端。
 *
 * 策略（任务 5）：
 * - baseURL 来自 NEXT_PUBLIC_API_BASE_URL（浏览器侧调用，必须为宿主机可访问的地址）
 * - withCredentials: true 让 refresh cookie 跨子域共享
 * - access token 仅存内存（authStore），通过请求拦截器注入 Authorization header
 * - 401 时自动触发 /api/auth/refresh，从 httpOnly cookie 读 refresh；失败则登出
 * - 并发 401 共享同一个 refresh Promise，避免重复刷新
 */

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

export const apiClient: AxiosInstance = axios.create({
  baseURL: API_BASE_URL,
  withCredentials: true,
  timeout: 30_000,
  headers: { "Content-Type": "application/json" },
});

// 注入 access token 的 hook（避免循环依赖：authStore 在使用 client 时再注入）
let accessTokenGetter: () => string | null = () => null;
let onUnauthorized: () => void = () => {};

export function configureAuth(opts: {
  getAccessToken: () => string | null;
  onUnauthorized: () => void;
}) {
  accessTokenGetter = opts.getAccessToken;
  onUnauthorized = opts.onUnauthorized;
}

apiClient.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const token = accessTokenGetter();
  if (token) {
    config.headers.set("Authorization", `Bearer ${token}`);
  }
  return config;
});

// ============================================================================
// 401 自动 refresh（共享 in-flight promise，避免并发刷新）
// ============================================================================

let refreshPromise: Promise<string> | null = null;

async function refreshAccessToken(): Promise<string> {
  const resp = await axios.post(`${API_BASE_URL}/api/auth/refresh`, null, {
    withCredentials: true,
  });
  const newAccess = resp.data?.access_token;
  if (!newAccess) {
    throw new Error("Refresh response missing access_token");
  }
  return newAccess as string;
}

apiClient.interceptors.response.use(
  (resp) => resp,
  async (error: AxiosError) => {
    const originalRequest = error.config as InternalAxiosRequestConfig & {
      _retry?: boolean;
    };

    // 非 401 或已重试过：直接抛
    if (error.response?.status !== 401 || originalRequest._retry) {
      return Promise.reject(error);
    }

    // 已是 /refresh 端点失败：放弃，触发登出
    if (originalRequest.url?.includes("/api/auth/refresh")) {
      onUnauthorized();
      return Promise.reject(error);
    }

    originalRequest._retry = true;

    if (!refreshPromise) {
      refreshPromise = refreshAccessToken().finally(() => {
        refreshPromise = null;
      });
    }

    try {
      const newAccess = await refreshPromise;
      // 通过 callback 注入回 store（避免循环依赖）
      // 这里我们直接写到 header，调用方负责持久化
      originalRequest.headers.set("Authorization", `Bearer ${newAccess}`);
      return apiClient(originalRequest);
    } catch (refreshError) {
      onUnauthorized();
      return Promise.reject(refreshError);
    }
  },
);

"use client";

import { useEffect, useRef, useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { configureAuth } from "@/lib/api/client";
import { useAuthStore } from "@/stores/authStore";
import { useBootstrapSession } from "@/hooks/useAuth";

// ============================================================================
// TanStack Query Client
// ============================================================================

function QueryProvider({ children }: { children: React.ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 30_000,
            retry: 1,
            refetchOnWindowFocus: false,
          },
        },
      }),
  );
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

// ============================================================================
// Auth Provider：注入 access token getter / 登出回调 + 启动时 refresh
// ============================================================================

function AuthBootstrap({ children }: { children: React.ReactNode }) {
  const configuredRef = useRef(false);

  // 注入 client 拦截器需要的回调（仅一次）
  useEffect(() => {
    if (configuredRef.current) return;
    configuredRef.current = true;
    configureAuth({
      getAccessToken: () => useAuthStore.getState().accessToken,
      onUnauthorized: () => useAuthStore.getState().logout(),
    });
  }, []);

  // 启动时尝试 refresh 恢复会话
  useBootstrapSession();

  return <>{children}</>;
}

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <QueryProvider>
      <AuthBootstrap>{children}</AuthBootstrap>
    </QueryProvider>
  );
}

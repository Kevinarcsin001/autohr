"use client";

/**
 * (app) 认证路由组守卫：未登录(guest)自动跳转登录页并记住来源，
 * 登录后回跳。修复:token 过期(401→refresh 失败→logout)后
 * 页面停留在「无法加载」而非引导重新登录。
 */
import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";

import { AppNav } from "@/components/AppNav";
import { useAuthStore } from "@/stores/authStore";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const status = useAuthStore((s) => s.status);

  useEffect(() => {
    if (status === "guest") {
      const from = encodeURIComponent(pathname ?? "/");
      router.replace(`/login?from=${from}`);
    }
  }, [status, pathname, router]);

  // 初始化中(status=loading)先渲染骨架,避免闪烁跳转
  if (status === "loading") {
    return (
      <div className="flex min-h-screen items-center justify-center text-sm text-muted-foreground">
        会话恢复中…
      </div>
    );
  }

  if (status === "guest") {
    return (
      <div className="flex min-h-screen items-center justify-center text-sm text-muted-foreground">
        正在跳转登录…
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-background">
      <AppNav />
      {children}
    </div>
  );
}

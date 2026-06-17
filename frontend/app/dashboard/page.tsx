"use client";

import Link from "next/link";
import { useLogout } from "@/hooks/useAuth";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { useAuthStore } from "@/stores/authStore";

export default function DashboardPage() {
  const user = useAuthStore((s) => s.user);
  const status = useAuthStore((s) => s.status);
  const logout = useLogout();

  if (status === "loading") {
    return <div className="p-8">加载中...</div>;
  }
  if (!user) {
    return (
      <div className="p-8">
        <p>未登录</p>
        <Link href="/login" className="text-primary underline">
          前往登录
        </Link>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-8">
      <header className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">欢迎，{user.name}</h1>
        <Button variant="outline" onClick={() => logout.mutate()} disabled={logout.isPending}>
          {logout.isPending ? "登出中..." : "登出"}
        </Button>
      </header>

      <Card>
        <CardHeader>
          <CardTitle>账户信息</CardTitle>
          <CardDescription>当前登录用户</CardDescription>
        </CardHeader>
        <CardContent className="space-y-1 text-sm">
          <div>
            <span className="font-medium">邮箱：</span>
            <span className="text-muted-foreground">{user.email}</span>
          </div>
          <div>
            <span className="font-medium">角色：</span>
            <span className="text-muted-foreground">
              {user.role === "admin" ? "团队管理员" : "普通成员"}
            </span>
          </div>
          <div>
            <span className="font-medium">团队：</span>
            <span className="text-muted-foreground">{user.team_id ?? "未加入"}</span>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>招聘工作台</CardTitle>
          <CardDescription>常用入口</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-3 text-sm">
          <Link
            href="/uploads"
            className="text-primary hover:underline"
          >
            简历上传中心 →
          </Link>
          <Link href="/jobs" className="text-primary hover:underline">
            职位管理 →
          </Link>
        </CardContent>
      </Card>

      {user.role === "admin" && (
        <Card>
          <CardHeader>
            <CardTitle>管理</CardTitle>
            <CardDescription>仅管理员可见</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-x-6 gap-y-2 text-sm">
            <Link
              href="/admin/members"
              className="text-primary hover:underline"
            >
              成员管理 →
            </Link>
            <Link
              href="/admin/email"
              className="text-primary hover:underline"
            >
              邮箱抓取配置 →
            </Link>
          </CardContent>
        </Card>
      )}

      <p className="text-sm text-muted-foreground">
        更多功能（简历上传、岗位管理、评分排名等）将在后续任务中陆续上线。
      </p>
    </div>
  );
}

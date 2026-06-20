"use client";

import Link from "next/link";
import { useState } from "react";

import { EmptyState } from "@/components/EmptyState";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { useAuditLogs } from "@/hooks/useAuditLogs";
import { useAuthStore } from "@/stores/authStore";
import { formatDateTime } from "@/lib/utils";
import { ScrollText } from "lucide-react";

export default function AuditLogsPage() {
  const user = useAuthStore((s) => s.user);
  const isAdmin = user?.role === "admin";
  const [page, setPage] = useState(1);
  const { data, isLoading, isError } = useAuditLogs(page, 50);

  if (!isAdmin) {
    return (
      <div className="p-8">
        <Alert variant="destructive">
          <AlertTitle>权限不足</AlertTitle>
          <AlertDescription>
            仅团队管理员可访问审计日志页面。
          </AlertDescription>
        </Alert>
      </div>
    );
  }

  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const totalPages = Math.ceil(total / 50);

  return (
    <div className="mx-auto max-w-5xl space-y-6 px-4 py-8 sm:px-6">
      <header>
        <h1 className="text-2xl font-bold">审计日志</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          团队内所有写操作记录（仅管理员可见）
        </p>
      </header>

      {isLoading && (
        <Card>
          <CardContent className="py-8 text-sm text-muted-foreground">
            加载中…
          </CardContent>
        </Card>
      )}

      {isError && (
        <Alert variant="destructive">
          <AlertTitle>加载失败</AlertTitle>
          <AlertDescription>无法加载审计日志，请稍后重试。</AlertDescription>
        </Alert>
      )}

      {!isLoading && !isError && items.length === 0 && (
        <EmptyState
          icon={ScrollText}
          title="暂无审计记录"
          description="团队操作记录将在此处展示。"
        />
      )}

      {items.length > 0 && (
        <>
          <div className="overflow-hidden rounded-lg border">
            <table className="w-full text-sm">
              <thead className="border-b bg-muted/50 text-left text-xs text-muted-foreground">
                <tr>
                  <th className="px-4 py-3">时间</th>
                  <th className="px-4 py-3">操作</th>
                  <th className="px-4 py-3">目标</th>
                  <th className="px-4 py-3">操作人</th>
                  <th className="px-4 py-3">IP</th>
                </tr>
              </thead>
              <tbody>
                {items.map((log) => (
                  <tr key={log.id} className="border-b last:border-0 hover:bg-accent/40">
                    <td className="px-4 py-2.5 font-mono text-xs">
                      {formatDateTime(log.created_at)}
                    </td>
                    <td className="px-4 py-2.5">
                      <code className="rounded bg-muted px-1 py-0.5 text-xs">
                        {log.action}
                      </code>
                    </td>
                    <td className="px-4 py-2.5 text-xs text-muted-foreground">
                      {log.target_type}
                      {log.target_id && (
                        <span className="ml-1 font-mono text-[10px]">
                          {log.target_id.slice(0, 8)}…
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-2.5 font-mono text-xs text-muted-foreground">
                      {log.actor_id?.slice(0, 8) ?? "—"}…
                    </td>
                    <td className="px-4 py-2.5 font-mono text-xs text-muted-foreground">
                      {log.ip ?? "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="flex items-center justify-between text-sm">
            <span className="text-muted-foreground">共 {total} 条</span>
            <div className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={page <= 1}
                onClick={() => setPage((p) => p - 1)}
              >
                上一页
              </Button>
              <span className="flex items-center text-muted-foreground">
                {page} / {totalPages || 1}
              </span>
              <Button
                variant="outline"
                size="sm"
                disabled={page >= totalPages}
                onClick={() => setPage((p) => p + 1)}
              >
                下一页
              </Button>
            </div>
          </div>
        </>
      )}

      <div>
        <Link href="/admin" className="text-sm text-primary hover:underline">
          ← 返回管理首页
        </Link>
      </div>
    </div>
  );
}

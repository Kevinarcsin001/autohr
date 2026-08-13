"use client";

import { Suspense, useCallback } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useInterviewSessions } from "@/hooks/useInterviewSessions";
import { formatDateTime } from "@/lib/utils";

const STATUS_OPTIONS = [
  { value: "", label: "全部" },
  { value: "scheduled", label: "待面试" },
  { value: "in_progress", label: "进行中" },
  { value: "completed", label: "已完成" },
];

const STATUS_VARIANT: Record<string, "default" | "secondary" | "success" | "warning"> = {
  scheduled: "secondary",
  in_progress: "warning",
  completed: "success",
};

const STATUS_LABEL: Record<string, string> = {
  scheduled: "待面试",
  in_progress: "进行中",
  completed: "已完成",
};

export default function InterviewsPage() {
  return (
    <Suspense fallback={<div className="p-8 text-sm">加载中...</div>}>
      <InterviewsContent />
    </Suspense>
  );
}

function InterviewsContent() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const status = searchParams.get("status") || "";
  const page = Number(searchParams.get("page") || "1");
  const pageSize = Number(searchParams.get("page_size") || "20");

  const { data, isLoading, isError, error } = useInterviewSessions({
    status: status || undefined,
    page,
    page_size: pageSize,
  });

  const updateUrl = useCallback(
    (updates: Record<string, string | number | null>) => {
      const next = new URLSearchParams(searchParams.toString());
      for (const [k, v] of Object.entries(updates)) {
        if (v === null || v === undefined || v === "") {
          next.delete(k);
        } else {
          next.set(k, String(v));
        }
      }
      router.replace(`/interviews?${next.toString()}`, { scroll: false });
    },
    [router, searchParams],
  );

  const handleStatusChange = useCallback(
    (newStatus: string) => updateUrl({ status: newStatus || null, page: 1 }),
    [updateUrl],
  );

  const items = data?.items ?? [];
  const total = data?.total ?? 0;

  return (
    <div className="mx-auto max-w-[1200px] space-y-4 p-6">
      <h1 className="text-2xl font-bold">面试管理</h1>

      {/* 状态筛选 */}
      <div className="flex items-center gap-2 text-sm">
        {STATUS_OPTIONS.map((opt) => (
          <Button
            key={opt.value}
            variant={status === opt.value ? "default" : "outline"}
            size="sm"
            onClick={() => handleStatusChange(opt.value)}
          >
            {opt.label}
          </Button>
        ))}
      </div>

      {/* 列表 */}
      {isError && (
        <Alert variant="destructive">
          <AlertTitle>加载失败</AlertTitle>
          <AlertDescription>
            {(error as Error)?.message ?? "请稍后重试"}
          </AlertDescription>
        </Alert>
      )}

      {isLoading && (
        <div className="py-8 text-sm text-muted-foreground">加载中...</div>
      )}

      {!isLoading && !isError && items.length === 0 && (
        <div className="py-16 text-center text-sm text-muted-foreground">
          暂无面试会话
          <p className="mt-1 text-xs">
            触发筛选流水线后，通过初筛的候选人会自动创建面试会话
          </p>
        </div>
      )}

      {!isLoading && items.length > 0 && (
        <div className="rounded-lg border">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b bg-muted/50">
                <th className="px-4 py-2.5 text-left font-medium">候选人</th>
                <th className="px-4 py-2.5 text-left font-medium">职位</th>
                <th className="px-4 py-2.5 text-left font-medium">面试官</th>
                <th className="px-4 py-2.5 text-left font-medium">状态</th>
                <th className="px-4 py-2.5 text-left font-medium">题目数</th>
                <th className="px-4 py-2.5 text-left font-medium">创建时间</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr
                  key={item.id}
                  className="cursor-pointer border-b transition-colors hover:bg-muted/30"
                  onClick={() => router.push(`/interviews/${item.id}`)}
                >
                  <td className="px-4 py-2.5 font-medium">
                    {item.candidate_name ?? "—"}
                  </td>
                  <td className="px-4 py-2.5 text-muted-foreground">
                    {item.job_title ?? "—"}
                  </td>
                  <td className="px-4 py-2.5 text-muted-foreground">
                    {item.interviewer_name ?? "未分配"}
                  </td>
                  <td className="px-4 py-2.5">
                    <Badge variant={STATUS_VARIANT[item.status] ?? "default"}>
                      {STATUS_LABEL[item.status] ?? item.status}
                    </Badge>
                  </td>
                  <td className="px-4 py-2.5 text-muted-foreground">
                    {item.question_count}
                  </td>
                  <td className="px-4 py-2.5 text-muted-foreground">
                    {item.created_at ? formatDateTime(item.created_at) : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* 简单分页 */}
      {total > pageSize && (
        <div className="flex items-center justify-center gap-2 text-sm">
          <Button
            variant="outline"
            size="sm"
            disabled={page <= 1}
            onClick={() => updateUrl({ page: page - 1 })}
          >
            上一页
          </Button>
          <span className="text-muted-foreground">
            第 {page} 页 / 共 {Math.ceil(total / pageSize)} 页（{total} 条）
          </span>
          <Button
            variant="outline"
            size="sm"
            disabled={page * pageSize >= total}
            onClick={() => updateUrl({ page: page + 1 })}
          >
            下一页
          </Button>
        </div>
      )}
    </div>
  );
}

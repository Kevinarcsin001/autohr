"use client";

import Link from "next/link";
import { useState } from "react";
import { Briefcase } from "lucide-react";
import { useJobs } from "@/hooks/useJobs";
import { useAuthStore } from "@/stores/authStore";
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
import type { JobStatus } from "@/lib/api/jobs";

const STATUS_TABS: Array<{ value: "" | JobStatus; label: string }> = [
  { value: "", label: "全部" },
  { value: "draft", label: "草稿" },
  { value: "active", label: "招聘中" },
  { value: "closed", label: "已关闭" },
];

const STATUS_LABEL: Record<JobStatus, string> = {
  draft: "草稿",
  active: "招聘中",
  closed: "已关闭",
};

const STATUS_BADGE: Record<JobStatus, string> = {
  draft: "bg-yellow-100 text-yellow-800",
  active: "bg-green-100 text-green-800",
  closed: "bg-gray-200 text-gray-700",
};

export default function JobsPage() {
  const user = useAuthStore((s) => s.user);
  const [statusFilter, setStatusFilter] = useState<"" | JobStatus>("");
  const [page, setPage] = useState(1);
  const pageSize = 20;
  const { data, isLoading, isError, error } = useJobs({
    status: statusFilter || undefined,
    page,
    page_size: pageSize,
  });

  if (!user) {
    return (
      <div className="p-8">
        <Alert>
          <AlertTitle>请先登录</AlertTitle>
          <AlertDescription>
            <Link href="/login" className="text-primary underline">
              前往登录
            </Link>
          </AlertDescription>
        </Alert>
      </div>
    );
  }

  if (user.team_id == null) {
    return (
      <div className="p-8">
        <Alert variant="destructive">
          <AlertTitle>未加入团队</AlertTitle>
          <AlertDescription>
            当前账号未关联任何团队，无法管理职位。
          </AlertDescription>
        </Alert>
      </div>
    );
  }

  const totalPages = data ? Math.max(1, Math.ceil(data.total / pageSize)) : 1;

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-8">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">职位管理</h1>
          <p className="text-sm text-muted-foreground">
            创建、编辑 JD 与硬性筛选条件
          </p>
        </div>
        <Button asChild>
          <Link href="/jobs/new">+ 新建职位</Link>
        </Button>
      </header>

      <div className="flex gap-2">
        {STATUS_TABS.map((tab) => (
          <Button
            key={tab.value}
            variant={statusFilter === tab.value ? "default" : "outline"}
            size="sm"
            onClick={() => {
              setStatusFilter(tab.value);
              setPage(1);
            }}
          >
            {tab.label}
          </Button>
        ))}
      </div>

      {isLoading && <div className="text-sm">加载中...</div>}
      {isError && (
        <Alert variant="destructive">
          <AlertTitle>无法加载职位列表</AlertTitle>
          <AlertDescription>
            {error instanceof Error ? error.message : "请稍后重试"}
          </AlertDescription>
        </Alert>
      )}

      {data && data.items.length === 0 && (
        <EmptyState
          icon={Briefcase}
          title="暂无职位"
          description={
            statusFilter
              ? "当前过滤条件下没有职位，可切换到其他状态。"
              : "创建职位并填写 JD 与硬性筛选条件，即可开始筛选候选人。"
          }
          action={{ label: "新建职位", href: "/jobs/new" }}
        />
      )}

      {data && data.items.length > 0 && (
        <div className="space-y-3">
          {data.items.map((job) => (
            <Card key={job.id}>
              <CardContent className="flex items-center justify-between py-4">
                <div className="space-y-1">
                  <div className="flex items-center gap-3">
                    <Link
                      href={`/jobs/${job.id}`}
                      className="text-base font-semibold hover:underline"
                    >
                      {job.title}
                    </Link>
                    <span
                      className={`rounded-full px-2 py-0.5 text-xs ${STATUS_BADGE[job.status]}`}
                    >
                      {STATUS_LABEL[job.status]}
                    </span>
                    <span className="text-xs text-muted-foreground">
                      v{job.current_version}
                    </span>
                  </div>
                  <div className="text-xs text-muted-foreground">
                    更新于 {new Date(job.updated_at).toLocaleString("zh-CN")}
                  </div>
                </div>
                <Button asChild variant="outline" size="sm">
                  <Link href={`/jobs/${job.id}`}>查看 / 编辑</Link>
                </Button>
              </CardContent>
            </Card>
          ))}

          {/* 分页 */}
          <div className="flex items-center justify-between">
            <div className="text-sm text-muted-foreground">
              共 {data.total} 条 · 第 {data.page} / {totalPages} 页
            </div>
            <div className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={page <= 1}
                onClick={() => setPage((p) => Math.max(1, p - 1))}
              >
                上一页
              </Button>
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
        </div>
      )}
    </div>
  );
}

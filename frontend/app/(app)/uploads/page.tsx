"use client";

import Link from "next/link";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { useUploads } from "@/hooks/useUploads";
import { useJobs } from "@/hooks/useJobs";
import { useAuthStore } from "@/stores/authStore";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { UploadDropzone } from "@/components/UploadDropzone";
import { UploadProgressList } from "@/components/UploadProgressList";

export default function UploadsPage() {
  const user = useAuthStore((s) => s.user);
  const status = useAuthStore((s) => s.status);
  const [selectedJobId, setSelectedJobId] = useState<string>("");
  const { data: jobsData, isLoading: jobsLoading } = useJobs();
  const router = useRouter();

  const { entries, addFiles, startAll, retry, remove, clearCompleted } =
    useUploads({
      concurrency: 4,
      jobId: selectedJobId || null,
    });

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

  const pendingCount = entries.filter(
    (e) => e.status === "queued" || e.status === "failed"
  ).length;
  const doneCount = entries.filter((e) => e.status === "done").length;
  const rejectedCount = entries.filter(
    (e) => e.status === "rejected"
  ).length;
  const inProgress = entries.some(
    (e) => e.status === "uploading" || e.status === "confirming"
  );

  const jobs = jobsData?.items ?? [];
  const hasDone = doneCount > 0;

  return (
    <div className="mx-auto max-w-3xl space-y-6 p-8">
      <header className="space-y-1">
        <h1 className="text-2xl font-bold">简历上传中心</h1>
        <p className="text-sm text-muted-foreground">
          批量上传候选人简历，选择关联职位后上传即自动入池
        </p>
      </header>

      {user.team_id === null && (
        <Alert variant="destructive">
          <AlertTitle>未加入团队</AlertTitle>
          <AlertDescription>
            上传简历需要先加入或创建团队。
          </AlertDescription>
        </Alert>
      )}

      {/* 职位选择 */}
      <Card>
        <CardHeader>
          <CardTitle>关联职位</CardTitle>
          <CardDescription>
            选择职位后上传的简历将自动加入该职位的候选人池
          </CardDescription>
        </CardHeader>
        <CardContent>
          <select
            value={selectedJobId}
            onChange={(e) => setSelectedJobId(e.target.value)}
            className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <option value="">不关联（仅上传）</option>
            {jobs.map((job) => (
              <option key={job.id} value={job.id}>
                {job.title} ({job.status === "active" ? "招聘中" : job.status === "draft" ? "草稿" : "已关闭"})
              </option>
            ))}
          </select>
          {jobs.length === 0 && (
            <p className="mt-2 text-xs text-muted-foreground">
              暂无职位，
              <Link href="/jobs/new" className="text-primary hover:underline">
                新建职位
              </Link>
            </p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>选择文件</CardTitle>
          <CardDescription>
            支持批量选择，单文件超限或扩展名非法会在客户端即时拒绝
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <UploadDropzone onFiles={(files) => addFiles(files)} />
          <div className="flex items-center gap-2">
            <Button
              onClick={() => startAll()}
              disabled={pendingCount === 0 || inProgress}
            >
              {inProgress
                ? "上传中..."
                : `开始上传（${pendingCount} 个待处理）`}
            </Button>
            {entries.length > 0 && (
              <span className="text-xs text-muted-foreground">
                共 {entries.length} 个 · 成功 {doneCount} · 拒绝 {rejectedCount}
              </span>
            )}
          </div>
        </CardContent>
      </Card>

      <UploadProgressList
        entries={entries}
        onRetry={retry}
        onRemove={remove}
        onClearCompleted={clearCompleted}
      />

      {/* 上传完成后引导去候选人页 */}
      {hasDone && selectedJobId && (
        <Alert>
          <AlertDescription className="flex items-center gap-3">
            上传完成！简历已关联到职位。
            <Button asChild size="sm" variant="default">
              <Link href={`/jobs/${selectedJobId}/candidates`}>
                查看候选人列表 →
              </Link>
            </Button>
          </AlertDescription>
        </Alert>
      )}

      <div className="text-sm text-muted-foreground">
        <Link href="/dashboard" className="text-primary hover:underline">
          ← 返回工作台
        </Link>
      </div>
    </div>
  );
}

"use client";

import Link from "next/link";
import { useUploads } from "@/hooks/useUploads";
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
  const { entries, addFiles, startAll, retry, remove, clearCompleted } =
    useUploads({
      concurrency: 4,
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

  return (
    <div className="mx-auto max-w-3xl space-y-6 p-8">
      <header className="space-y-1">
        <h1 className="text-2xl font-bold">简历上传中心</h1>
        <p className="text-sm text-muted-foreground">
          批量上传候选人简历，系统将自动嗅探 MIME 类型并加入解析队列
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

      <div className="text-sm text-muted-foreground">
        <Link href="/dashboard" className="text-primary hover:underline">
          ← 返回工作台
        </Link>
      </div>
    </div>
  );
}

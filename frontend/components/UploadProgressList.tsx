"use client";

import { Button } from "@/components/ui/button";
import {
  reasonToLabel,
  type UploadFileEntry,
  type UploadFileStatus,
} from "@/hooks/useUploads";
import { cn } from "@/lib/utils";

const STATUS_LABEL: Record<UploadFileStatus, string> = {
  queued: "排队中",
  uploading: "上传中",
  confirming: "校验中",
  done: "完成",
  rejected: "已拒绝",
  failed: "失败",
};

const STATUS_BADGE: Record<UploadFileStatus, string> = {
  queued: "bg-muted text-muted-foreground",
  uploading: "bg-blue-100 text-blue-900",
  confirming: "bg-purple-100 text-purple-900",
  done: "bg-green-100 text-green-900",
  rejected: "bg-amber-100 text-amber-900",
  failed: "bg-red-100 text-red-900",
};

interface UploadProgressListProps {
  entries: UploadFileEntry[];
  onRetry: (localId: string) => void;
  onRemove: (localId: string) => void;
  onClearCompleted: () => void;
}

export function UploadProgressList({
  entries,
  onRetry,
  onRemove,
  onClearCompleted,
}: UploadProgressListProps) {
  if (entries.length === 0) return null;

  const hasCompleted = entries.some(
    (e) => e.status === "done" || e.status === "rejected"
  );

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold">
          上传队列（{entries.length}）
        </h3>
        {hasCompleted && (
          <Button variant="ghost" size="sm" onClick={onClearCompleted}>
            清除已完成
          </Button>
        )}
      </div>
      <ul className="space-y-2">
        {entries.map((e) => (
          <li
            key={e.localId}
            className="flex items-center gap-3 rounded-md border bg-card p-3"
          >
            <div className="min-w-0 flex-1 space-y-1">
              <div className="flex items-center gap-2">
                <span
                  className={cn(
                    "rounded px-1.5 py-0.5 text-[10px] font-medium",
                    STATUS_BADGE[e.status]
                  )}
                >
                  {STATUS_LABEL[e.status]}
                </span>
                <span className="truncate text-sm font-medium">
                  {e.file.name}
                </span>
                <span className="text-xs text-muted-foreground">
                  {(e.file.size / 1024).toFixed(1)} KB
                </span>
              </div>

              {(e.status === "uploading" || e.status === "confirming") && (
                <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
                  <div
                    className={cn(
                      "h-full bg-primary transition-all",
                      e.status === "confirming" && "bg-purple-500"
                    )}
                    style={{ width: `${e.progress}%` }}
                  />
                </div>
              )}

              {(e.status === "rejected" || e.status === "failed") && e.reason && (
                <p className="text-xs text-destructive">
                  {reasonToLabel(e.reason)}
                </p>
              )}
              {e.status === "done" && e.resumeId && (
                <p className="text-xs text-muted-foreground">
                  已写入简历 {e.resumeId.slice(0, 8)}… 已入队解析
                </p>
              )}
            </div>

            <div className="flex shrink-0 items-center gap-1">
              {(e.status === "failed" || e.status === "rejected") && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => onRetry(e.localId)}
                >
                  重试
                </Button>
              )}
              <Button
                variant="ghost"
                size="sm"
                onClick={() => onRemove(e.localId)}
              >
                移除
              </Button>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

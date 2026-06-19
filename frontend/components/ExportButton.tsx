"use client";

import { useCallback, useRef, useState } from "react";
import { Download, Loader2 } from "lucide-react";

import {
  getDownloadUrlApi,
  getExportStatusApi,
  requestExportApi,
  type ExportFormat,
  type ExportFilters,
} from "@/lib/api/exports";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

// ============================================================================
// 常量
// ============================================================================

/** 异步任务轮询间隔（ms）。后端 worker 通常秒级完成；2s 平衡体验/压力。 */
const POLL_INTERVAL_MS = 2000;
/** 最大轮询时长（避免无限等待）。后端有 MAX_ATTEMPTS 重试 + 退避，2 分钟兜底。 */
const POLL_MAX_DURATION_MS = 120_000;

// ============================================================================
// 组件
// ============================================================================

interface ExportButtonProps {
  jobId: string;
  /** 可选过滤（与候选人列表当前筛选保持一致） */
  filters?: ExportFilters;
  format?: ExportFormat;
  /** 触发额外副作用（如审计埋点） */
  onSuccess?: (fileKey: string) => void;
  className?: string;
  /** 按钮文案；默认 "导出 Excel" */
  label?: string;
}

type Phase = "idle" | "sync" | "polling" | "downloading" | "done" | "error";

/**
 * 任务 22：导出按钮
 *
 * 设计约束（来自 design.md）：
 * - 不在前端直连对象存储：所有 URL 通过后端签名（5min 过期）
 * - 行数 > 5000 后端自动转异步 → 前端轮询 GET /api/exports/jobs/{job_id}
 * - 同步路径：拿到 download_url → 调 GET /api/exports/download 拿新 URL → 浏览器下载
 *   （不直接用 sync response 的 download_url：让它走统一入口，便于将来加审计/撤销）
 */
export function ExportButton({
  jobId,
  filters,
  format = "xlsx",
  onSuccess,
  className,
  label = "导出 Excel",
}: ExportButtonProps) {
  const [phase, setPhase] = useState<Phase>("idle");
  const [message, setMessage] = useState<string>("");
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const cleanupTimer = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const triggerDownload = useCallback(
    async (fileKey: string) => {
      try {
        setPhase("downloading");
        // 拿新的 5min URL（即使 sync response 已含 URL，也走 /download 端点统一）
        const { download_url } = await getDownloadUrlApi(fileKey);
        // 浏览器原生下载（避免 axios 拉 blob 占内存）
        const a = document.createElement("a");
        a.href = download_url;
        a.download = ""; // 让浏览器从 Content-Disposition 决定
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        setPhase("done");
        setMessage("已开始下载");
        onSuccess?.(fileKey);
        // 3s 后回到 idle，便于再次点击
        setTimeout(() => setPhase("idle"), 3000);
      } catch (err) {
        setPhase("error");
        setMessage(
          err instanceof Error ? err.message : "下载链接生成失败"
        );
      }
    },
    [onSuccess]
  );

  const pollStatus = useCallback(
    async (asyncJobId: string, startedAt: number) => {
      if (Date.now() - startedAt > POLL_MAX_DURATION_MS) {
        cleanupTimer();
        setPhase("error");
        setMessage("导出超时，请稍后重试");
        return;
      }

      try {
        const status = await getExportStatusApi(asyncJobId);
        if (status.status === "success" && status.file_key) {
          cleanupTimer();
          setMessage(
            `导出完成（${status.row_count ?? 0} 行，${Math.round(
              (status.file_size ?? 0) / 1024
            )} KB）`
          );
          await triggerDownload(status.file_key);
          return;
        }
        if (status.status === "failed") {
          cleanupTimer();
          setPhase("error");
          setMessage(status.error ?? "导出失败");
          return;
        }
        // queued / running / retry → 继续轮询
        setMessage(
          status.status === "running"
            ? "正在生成 Excel..."
            : `任务排队中（${status.row_count ?? 0} 行）`
        );
        timerRef.current = setTimeout(
          () => pollStatus(asyncJobId, startedAt),
          POLL_INTERVAL_MS
        );
      } catch (err) {
        cleanupTimer();
        setPhase("error");
        setMessage(
          err instanceof Error ? err.message : "查询导出状态失败"
        );
      }
    },
    [cleanupTimer, triggerDownload]
  );

  const handleClick = useCallback(async () => {
    if (phase === "sync" || phase === "polling" || phase === "downloading") {
      return; // 防重复点击
    }
    setPhase("sync");
    setMessage("正在请求导出...");

    try {
      const result = await requestExportApi({
        job_id: jobId,
        format,
        filters,
      });

      if (result.mode === "sync") {
        setMessage(
          `生成完成（${result.row_count} 行，${Math.round(
            result.file_size / 1024
          )} KB）`
        );
        await triggerDownload(result.file_key);
        return;
      }

      // 异步路径：轮询直到 success
      setPhase("polling");
      setMessage(`数据量较大（${result.row_count} 行），正在后台生成...`);
      pollStatus(result.job_id, Date.now());
    } catch (err) {
      setPhase("error");
      setMessage(
        err instanceof Error ? err.message : "请求导出失败"
      );
    }
  }, [phase, jobId, format, filters, triggerDownload, pollStatus]);

  const isBusy =
    phase === "sync" || phase === "polling" || phase === "downloading";

  return (
    <div className="flex items-center gap-3">
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={isBusy}
        onClick={handleClick}
        className={cn(className)}
      >
        {isBusy ? (
          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
        ) : (
          <Download className="mr-2 h-4 w-4" />
        )}
        {label}
      </Button>
      {message && (
        <span
          className={cn(
            "text-xs",
            phase === "error" ? "text-destructive" : "text-muted-foreground"
          )}
        >
          {message}
        </span>
      )}
    </div>
  );
}

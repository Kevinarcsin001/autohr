"use client";

import { useState } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { useResumeUrl } from "@/hooks/useResumeUrl";

/**
 * 简历预览（任务 24）。
 *
 * 策略（plan）：
 * - PDF → 浏览器原生 iframe viewer（不引入 pdfjs-dist）
 * - 图片 → <img>
 * - 其他 / 加载失败 → 降级为下载按钮
 * - 5s 超时未加载完 → 显示降级提示
 * - signed_url 由 useResumeUrl 自动管理过期前 30s prefetch
 */

interface ResumePreviewProps {
  candidateId: string;
  mimeType?: string | null;
}

const PDF_LOAD_TIMEOUT_MS = 5_000;

export function ResumePreview({
  candidateId,
  mimeType,
}: ResumePreviewProps) {
  const { data, isLoading, isError } = useResumeUrl(candidateId);
  const [loadFailed, setLoadFailed] = useState(false);
  const [timedOut, setTimedOut] = useState(false);

  if (isLoading) {
    return (
      <div className="flex h-96 items-center justify-center text-sm text-muted-foreground">
        正在加载简历预览...
      </div>
    );
  }

  if (isError || !data) {
    return (
      <Alert variant="destructive">
        <AlertTitle>无法加载简历</AlertTitle>
        <AlertDescription>
          简历不存在或加载失败，请稍后重试。
        </AlertDescription>
      </Alert>
    );
  }

  const effectiveMime = mimeType || data.mime_type || "";
  const isPdf =
    effectiveMime.includes("pdf") || data.url.toLowerCase().includes(".pdf");
  const isImage =
    effectiveMime.startsWith("image/") ||
    /\.(png|jpe?g|webp|gif)$/i.test(data.url);

  // 已知不支持类型 → 直接走下载降级
  if (!isPdf && !isImage) {
    return <DownloadFallback url={data.url} />;
  }

  // 加载失败或超时 → 降级
  if (loadFailed || timedOut) {
    return <DownloadFallback url={data.url} reason="load-failed" />;
  }

  return (
    <div className="relative h-[70vh] w-full overflow-hidden rounded-md border">
      {isPdf && (
        <PdfFrame
          url={data.url}
          onLoad={() => setTimedOut(false)}
          onError={() => setLoadFailed(true)}
          onTimeout={() => setTimedOut(true)}
        />
      )}
      {isImage && (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={data.url}
          alt="简历预览"
          className="h-full w-full object-contain"
          onError={() => setLoadFailed(true)}
        />
      )}
    </div>
  );
}

// ============================================================================
// PDF iframe（带 5s 超时降级）
// ============================================================================

function PdfFrame({
  url,
  onLoad,
  onError,
  onTimeout,
}: {
  url: string;
  onLoad: () => void;
  onError: () => void;
  onTimeout: () => void;
}) {
  const [loaded, setLoaded] = useState(false);

  // iframe 没有可靠的 onError；用 onLoad + 超时组合
  useLoadTimeout(loaded, PDF_LOAD_TIMEOUT_MS, onTimeout);

  return (
    <iframe
      src={url}
      title="简历预览"
      className="h-full w-full border-0"
      onLoad={() => {
        setLoaded(true);
        onLoad();
      }}
      onError={onError}
    />
  );
}

// ============================================================================
// 下载降级
// ============================================================================

function DownloadFallback({
  url,
  reason = "unsupported-type",
}: {
  url: string;
  reason?: "unsupported-type" | "load-failed";
}) {
  return (
    <Alert>
      <AlertTitle>
        {reason === "load-failed" ? "无法预览" : "暂不支持在线预览"}
      </AlertTitle>
      <AlertDescription className="flex items-center gap-3">
        <span>请下载后查看。</span>
        <Button asChild size="sm">
          <a href={url} download>
            下载简历
          </a>
        </Button>
      </AlertDescription>
    </Alert>
  );
}

// ============================================================================
// Hook：超时检测
// ============================================================================

import { useEffect } from "react";

function useLoadTimeout(
  loaded: boolean,
  timeoutMs: number,
  onTimeout: () => void,
) {
  useEffect(() => {
    if (loaded) return;
    const t = setTimeout(onTimeout, timeoutMs);
    return () => clearTimeout(t);
  }, [loaded, timeoutMs, onTimeout]);
}

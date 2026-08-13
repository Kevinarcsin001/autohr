"use client";

import { useEffect, useState } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { useResumeUrl } from "@/hooks/useResumeUrl";
import { cn } from "@/lib/utils";

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
  parsedText?: string | null;
}

export function ResumePreview({
  candidateId,
  mimeType,
  parsedText,
}: ResumePreviewProps) {
  const { data, isLoading, isError } = useResumeUrl(candidateId);
  const [loadFailed, setLoadFailed] = useState(false);
  const [viewMode, setViewMode] = useState<"text" | "file">("file");
  const [pdfBlobUrl, setPdfBlobUrl] = useState<string | null>(null);

  // 从 URL 路径中提取扩展名（签名 URL 可能带 ?query）
  const urlPath = data?.url ? data.url.split("?")[0].toLowerCase() : "";
  const effectiveMime = mimeType || data?.mime_type || "";
  const isPdf =
    effectiveMime.includes("pdf") || urlPath.endsWith(".pdf");
  const isImage = data?.url
    ? effectiveMime.startsWith("image/") ||
      /\.(png|jpe?g|webp|gif|bmp|tiff?)(\?|$)/i.test(data.url) ||
      /\.(png|jpe?g|webp|gif|bmp|tiff?)$/i.test(urlPath)
    : false;

  useEffect(() => {
    if (!data?.url || !isPdf) return;
    let active = true;
    let currentBlobUrl: string | null = null;

    fetch(data.url)
      .then((res) => {
        if (!res.ok) throw new Error("Failed to fetch PDF file");
        return res.blob();
      })
      .then((blob) => {
        if (!active) return;
        const pdfBlob = new Blob([blob], { type: "application/pdf" });
        const url = URL.createObjectURL(pdfBlob);
        currentBlobUrl = url;
        setPdfBlobUrl(url);
      })
      .catch((err) => {
        console.error("PDF blob load failed:", err);
        if (active) {
          setLoadFailed(true);
        }
      });

    return () => {
      active = false;
      if (currentBlobUrl) {
        URL.revokeObjectURL(currentBlobUrl);
      }
    };
  }, [data?.url, isPdf]);

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

  if (isPdf && !pdfBlobUrl && !loadFailed && viewMode === "file") {
    return (
      <div className="flex h-[70vh] items-center justify-center text-sm text-muted-foreground border rounded-md bg-slate-50 dark:bg-slate-900">
        正在准备简历预览...
      </div>
    );
  }

  // 如果选择文本视图且有解析文本，直接渲染文本视图以避免自动下载 PDF/文件
  if (viewMode === "text" && parsedText) {
    return (
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <span className="text-xs text-muted-foreground bg-muted/45 px-3 py-1.5 rounded-md flex-1 mr-4 truncate">
            当前展示解析文本。如需查看排版或下载，请切换至“文件视图”或
            <a href={data.url} download className="text-primary hover:underline font-medium ml-1">
              下载原始文件
            </a>
          </span>
          <div className="inline-flex rounded-lg border bg-muted p-0.5 text-xs shrink-0">
            <button
              type="button"
              className="rounded-md px-3 py-1 font-medium transition-colors bg-background text-foreground shadow-sm"
              onClick={() => setViewMode("text")}
            >
              文本视图
            </button>
            <button
              type="button"
              className="rounded-md px-3 py-1 font-medium transition-colors text-muted-foreground hover:text-foreground"
              onClick={() => setViewMode("file")}
            >
              文件视图
            </button>
          </div>
        </div>
        <ParsedTextPreview text={parsedText} />
      </div>
    );
  }

  // 已知不支持类型
  if (!isPdf && !isImage) {
    if (parsedText) {
      return (
        <div className="space-y-2">
          <div className="flex items-center justify-between text-xs text-muted-foreground bg-muted/45 px-3 py-1.5 rounded-md">
            <span>当前格式暂不支持在线文件预览，已直接为您展示解析文本</span>
            <a href={data.url} download className="text-primary hover:underline font-medium">
              下载原始文件
            </a>
          </div>
          <ParsedTextPreview text={parsedText} />
        </div>
      );
    }
    return <DownloadFallback url={data.url} />;
  }

  // 加载失败 → 降级
  if (loadFailed) {
    if (parsedText) {
      return (
        <div className="space-y-2">
          <div className="flex items-center justify-between text-xs text-muted-foreground bg-muted/45 px-3 py-1.5 rounded-md">
            <span>在线文件预览失败，已为您加载解析文本</span>
            <a href={data.url} download className="text-primary hover:underline font-medium">
              下载原始文件
            </a>
          </div>
          <ParsedTextPreview text={parsedText} />
        </div>
      );
    }
    return <DownloadFallback url={data.url} reason="load-failed" />;
  }

  return (
    <div className="space-y-2">
      {parsedText && (
        <div className="flex justify-end">
          <div className="inline-flex rounded-lg border bg-muted p-0.5 text-xs">
            <button
              type="button"
              className={cn(
                "rounded-md px-3 py-1 font-medium transition-colors",
                viewMode === "text"
                  ? "bg-background text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground"
              )}
              onClick={() => setViewMode("text")}
            >
              文本视图
            </button>
            <button
              type="button"
              className={cn(
                "rounded-md px-3 py-1 font-medium transition-colors",
                viewMode === "file"
                  ? "bg-background text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground"
              )}
              onClick={() => setViewMode("file")}
            >
              文件视图
            </button>
          </div>
        </div>
      )}
      <div className="relative h-[70vh] w-full overflow-hidden rounded-md border bg-slate-50 dark:bg-slate-900">
        {isPdf && pdfBlobUrl && (
          <PdfFrame
            url={pdfBlobUrl}
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
    </div>
  );
}

function ParsedTextPreview({ text }: { text: string }) {
  return (
    <div className="h-[70vh] w-full overflow-y-auto bg-background p-6 font-sans text-sm leading-relaxed whitespace-pre-wrap rounded-md border text-foreground">
      {text}
    </div>
  );
}

function PdfFrame({ url }: { url: string }) {
  return (
    <iframe
      src={url}
      title="简历预览"
      className="h-full w-full border-0"
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



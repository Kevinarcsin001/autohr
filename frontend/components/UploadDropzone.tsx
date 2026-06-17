"use client";

import { useCallback, type DragEvent } from "react";
import { useDropzone } from "react-dropzone";
import { cn } from "@/lib/utils";

const ACCEPTED_TYPES: Record<string, string[]> = {
  "application/pdf": [".pdf"],
  "application/msword": [".doc"],
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document": [
    ".docx",
  ],
  "image/png": [".png"],
  "image/jpeg": [".jpg", ".jpeg"],
};

const MAX_SIZE_MB = 20;

interface UploadDropzoneProps {
  onFiles: (files: File[]) => void;
  disabled?: boolean;
}

export function UploadDropzone({
  onFiles,
  disabled,
}: UploadDropzoneProps) {
  const onDrop = useCallback(
    (accepted: File[]) => {
      if (accepted.length > 0) onFiles(accepted);
    },
    [onFiles]
  );

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    disabled,
    // maxSize 在客户端做拦截（仅警告，最终仍由 service 兜底）
    // 不传 maxSize：避免 dropzone 把超限文件静默丢弃，让 hook 统一处理
    accept: ACCEPTED_TYPES,
    multiple: true,
    noClick: false,
    noKeyboard: false,
  });

  // 阻止 rootProps 把 FileList 传成 onFiles（保持接口一致）
  const handleRootDrop = (e: DragEvent<HTMLDivElement>) => {
    // 让 dropzone 内部处理；这里仅做兼容保护
    if (disabled) e.preventDefault();
  };

  return (
    <div
      {...getRootProps()}
      onDrop={handleRootDrop}
      className={cn(
        "flex min-h-[180px] cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed border-muted-foreground/30 bg-muted/20 p-8 text-center transition-colors hover:border-primary/60 hover:bg-muted/40",
        isDragActive && "border-primary bg-primary/5",
        disabled && "pointer-events-none opacity-50"
      )}
    >
      <input {...getInputProps()} />
      <div className="space-y-2">
        <p className="text-base font-medium">
          {isDragActive ? "松开鼠标即可上传" : "拖拽简历文件到此处"}
        </p>
        <p className="text-sm text-muted-foreground">
          或点击选择文件 · 支持 PDF / DOC / DOCX / PNG / JPG · 单文件 ≤ {MAX_SIZE_MB} MB
        </p>
        <p className="text-xs text-muted-foreground">
          单批最多 100 份 · 并发 4 个上传
        </p>
      </div>
    </div>
  );
}

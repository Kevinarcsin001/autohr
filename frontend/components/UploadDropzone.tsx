"use client";

import { useCallback, useRef, type ChangeEvent, type DragEvent } from "react";
import { cn } from "@/lib/utils";

const ACCEPTED_MIME = "application/pdf,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document,image/png,image/jpeg";
const MAX_SIZE_MB = 20;

interface UploadDropzoneProps {
  onFiles: (files: File[]) => void;
  disabled?: boolean;
}

export function UploadDropzone({
  onFiles,
  disabled,
}: UploadDropzoneProps) {
  const inputRef = useRef<HTMLInputElement>(null);

  const handleChange = useCallback(
    (e: ChangeEvent<HTMLInputElement>) => {
      const files = e.target.files;
      if (files && files.length > 0) {
        onFiles(Array.from(files));
      }
      // Reset so the same file can be selected again
      e.target.value = "";
    },
    [onFiles]
  );

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      if (disabled) return;
      const files = e.dataTransfer.files;
      if (files && files.length > 0) {
        onFiles(Array.from(files));
      }
    },
    [disabled, onFiles]
  );

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
  }, []);

  const handleClick = () => {
    if (!disabled && inputRef.current) {
      inputRef.current.click();
    }
  };

  return (
    <div
      onClick={handleClick}
      onDrop={handleDrop}
      onDragOver={handleDragOver}
      className={cn(
        "flex min-h-[180px] cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed border-muted-foreground/30 bg-muted/20 p-8 text-center transition-colors hover:border-primary/60 hover:bg-muted/40",
        disabled && "pointer-events-none opacity-50"
      )}
    >
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPTED_MIME}
        multiple
        onChange={handleChange}
        className="hidden"
      />
      <div className="space-y-2">
        <p className="text-base font-medium">
          拖拽简历文件到此处
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

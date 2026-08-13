"use client";

import { useEffect, type ReactNode } from "react";

/**
 * 极简 Dialog：手写 fixed overlay（项目无 shadcn Dialog 依赖）。
 * 复用 OverrideDialog 的模式：ESC 关闭 + 点遮罩关闭 + 内容 stopPropagation。
 */

interface DialogProps {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: string;
  children: ReactNode;
  footer?: ReactNode;
  /** 内容区最大宽度（Tailwind class），默认 max-w-lg。 */
  maxWidth?: string;
}

export function Dialog({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  maxWidth = "max-w-lg",
}: DialogProps) {
  // ESC 关闭
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-label={title}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onClick={onClose}
    >
      <div
        className={`w-full ${maxWidth} overflow-hidden rounded-lg bg-background shadow-lg`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="border-b p-4">
          <h3 className="text-base font-medium">{title}</h3>
          {description && (
            <p className="mt-1 text-xs text-muted-foreground">{description}</p>
          )}
        </div>
        <div className="space-y-4 p-4">{children}</div>
        {footer && <div className="flex justify-end gap-2 border-t p-3">{footer}</div>}
      </div>
    </div>
  );
}

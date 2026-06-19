"use client";

import { useEffect, useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

/**
 * 改判弹窗（任务 24）。
 *
 * 复用 plan: app/jobs/[id]/page.tsx 的 fixed overlay 模式。
 * - reason 必填，1-500 字符
 * - new_disqualified 必填
 * - new_reasons 可选，逗号分隔（前端拆为数组）
 * - ESC 关闭；点遮罩关闭
 */

interface OverrideDialogProps {
  open: boolean;
  defaultDisqualified?: boolean;
  onClose: () => void;
  onSubmit: (payload: {
    new_disqualified: boolean;
    new_reasons: string[] | null;
    reason: string;
  }) => void;
  submitting?: boolean;
  error?: string | null;
}

export function OverrideDialog({
  open,
  defaultDisqualified = false,
  onClose,
  onSubmit,
  submitting = false,
  error = null,
}: OverrideDialogProps) {
  const [newDisqualified, setNewDisqualified] = useState(
    defaultDisqualified,
  );
  const [newReasons, setNewReasons] = useState("");
  const [reason, setReason] = useState("");

  // default 变化时同步（弹窗每次打开重置）
  useEffect(() => {
    if (open) {
      setNewDisqualified(defaultDisqualified);
      setNewReasons("");
      setReason("");
    }
  }, [open, defaultDisqualified]);

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

  const trimmedReason = reason.trim();
  const reasonValid = trimmedReason.length >= 1 && trimmedReason.length <= 500;
  const canSubmit = reasonValid && !submitting;

  const handleSubmit = () => {
    if (!canSubmit) return;
    const reasonsArray = newReasons
      .split(/[,，;；\n]/)
      .map((s) => s.trim())
      .filter(Boolean);
    onSubmit({
      new_disqualified: newDisqualified,
      new_reasons: reasonsArray.length > 0 ? reasonsArray : null,
      reason: trimmedReason,
    });
  };

  return (
    <div
      role="dialog"
      aria-label="HR 改判候选人"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-lg overflow-hidden rounded-lg bg-background shadow-lg"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="border-b p-4">
          <h3 className="text-base font-medium">HR 改判候选人</h3>
          <p className="mt-1 text-xs text-muted-foreground">
            改判将记录到审计日志，候选人结果会立即更新。
          </p>
        </div>

        <div className="space-y-4 p-4">
          <div>
            <Label className="text-sm">改判结果</Label>
            <div className="mt-2 flex gap-2">
              <button
                type="button"
                onClick={() => setNewDisqualified(false)}
                className={
                  "flex-1 rounded-md border px-3 py-2 text-sm " +
                  (newDisqualified === false
                    ? "border-emerald-500 bg-emerald-50 text-emerald-700"
                    : "hover:bg-accent")
                }
              >
                通过
              </button>
              <button
                type="button"
                onClick={() => setNewDisqualified(true)}
                className={
                  "flex-1 rounded-md border px-3 py-2 text-sm " +
                  (newDisqualified === true
                    ? "border-red-500 bg-red-50 text-red-700"
                    : "hover:bg-accent")
                }
              >
                淘汰
              </button>
            </div>
          </div>

          <div>
            <Label htmlFor="ov-reasons" className="text-sm">
              改判理由标签（可选，逗号分隔）
            </Label>
            <Input
              id="ov-reasons"
              value={newReasons}
              onChange={(e) => setNewReasons(e.target.value)}
              placeholder="如：HR 复核通过, 面试表现优异"
              className="mt-1"
            />
          </div>

          <div>
            <Label htmlFor="ov-reason" className="text-sm">
              改判说明 <span className="text-destructive">*</span>
            </Label>
            <textarea
              id="ov-reason"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="详细说明改判原因（1-500 字）"
              maxLength={500}
              rows={3}
              className="mt-1 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            />
            <div className="mt-1 flex justify-between text-xs text-muted-foreground">
              <span>{trimmedReason.length === 0 && "必填"}</span>
              <span>{trimmedReason.length} / 500</span>
            </div>
          </div>

          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
        </div>

        <div className="flex justify-end gap-2 border-t p-3">
          <Button variant="outline" onClick={onClose} disabled={submitting}>
            取消
          </Button>
          <Button onClick={handleSubmit} disabled={!canSubmit}>
            {submitting ? "提交中..." : "确认改判"}
          </Button>
        </div>
      </div>
    </div>
  );
}

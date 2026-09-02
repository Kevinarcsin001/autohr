"use client";

import { useEffect, useState } from "react";

import { FINAL_STATUS_LABELS, FinalStatus } from "@/lib/api/outcome";
import { useOutcome, useUpsertOutcome } from "@/hooks/useOutcome";

const STATUS_OPTIONS = Object.entries(FINAL_STATUS_LABELS) as [
  FinalStatus,
  string,
][];

/**
 * 用人结果选择器：HR 在候选人详情页录入 ground truth（效果回流闭环）。
 */
export function OutcomePicker({
  jobId,
  candidateId,
}: {
  jobId: string;
  candidateId: string;
}) {
  const { data: outcome } = useOutcome(jobId, candidateId);
  const upsert = useUpsertOutcome(jobId, candidateId);
  const [status, setStatus] = useState<FinalStatus | "">("");
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (outcome) {
      setStatus(outcome.final_status);
    }
  }, [outcome]);

  const dirty = status !== "" && status !== (outcome?.final_status ?? "");

  return (
    <div className="flex items-center gap-2">
      <select
        value={status}
        onChange={(e) => {
          setStatus(e.target.value as FinalStatus | "");
          setSaved(false);
        }}
        disabled={upsert.isPending}
        className="h-8 rounded-md border bg-background px-2 text-sm"
        aria-label="用人结果"
      >
        <option value="" disabled>
          {outcome ? FINAL_STATUS_LABELS[outcome.final_status] : "标记用人结果"}
        </option>
        {STATUS_OPTIONS.map(([value, label]) => (
          <option key={value} value={value}>
            {label}
          </option>
        ))}
      </select>
      {dirty && (
        <button
          type="button"
          className="rounded-md bg-primary px-2.5 py-1 text-xs font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
          disabled={upsert.isPending}
          onClick={() => {
            if (status) {
              upsert.mutate(
                { final_status: status },
                {
                  onSuccess: () => setSaved(true),
                },
              );
            }
          }}
        >
          {upsert.isPending ? "保存中…" : "保存"}
        </button>
      )}
      {saved && <span className="text-xs text-muted-foreground">已记录</span>}
    </div>
  );
}

"use client";

import { useCallback, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  approveCandidateApi,
  rejectCandidateApi,
} from "@/lib/api/screening";
import { type CandidateListItem } from "@/lib/api/candidates";
import { useCandidates } from "@/hooks/useCandidates";
import { useJob } from "@/hooks/useJobs";
import { formatDateTime } from "@/lib/utils";

export default function ReviewPage() {
  const params = useParams<{ id: string }>();
  const jobId = params.id;
  const router = useRouter();

  const { data: job, isLoading: jobLoading } = useJob(jobId);

  // 获取通过 AI 筛选的候选人
  const candidateParams = { page: 1, page_size: 200, group: "passed" as const };
  const { data, isLoading, isError, error, refetch } = useCandidates(jobId, candidateParams);

  const [actionState, setActionState] = useState<Record<string, { loading: boolean; result?: string }>>({});

  const handleApprove = useCallback(
    async (item: CandidateListItem) => {
      if (!item.screening_id) return;
      setActionState((prev) => ({
        ...prev,
        [item.id]: { loading: true },
      }));
      try {
        const res = await approveCandidateApi(item.screening_id, "HR 审核通过");
        setActionState((prev) => ({
          ...prev,
          [item.id]: { loading: false, result: `已通过，${res.question_count ?? "?"} 道面试题已生成` },
        }));
        refetch();
      } catch (err) {
        setActionState((prev) => ({
          ...prev,
          [item.id]: {
            loading: false,
            result: (err as Error)?.message ?? "操作失败",
          },
        }));
      }
    },
    [refetch],
  );

  const handleReject = useCallback(
    async (item: CandidateListItem, reason?: string) => {
      if (!item.screening_id) return;
      setActionState((prev) => ({
        ...prev,
        [item.id]: { loading: true },
      }));
      try {
        await rejectCandidateApi(item.screening_id, reason ?? "HR 评估淘汰");
        setActionState((prev) => ({
          ...prev,
          [item.id]: { loading: false, result: "已淘汰" },
        }));
        refetch();
      } catch (err) {
        setActionState((prev) => ({
          ...prev,
          [item.id]: {
            loading: false,
            result: (err as Error)?.message ?? "操作失败",
          },
        }));
      }
    },
    [refetch],
  );

  if (jobLoading) {
    return <div className="p-8 text-sm">加载中...</div>;
  }

  if (!job) {
    return (
      <div className="p-8">
        <Alert variant="destructive">
          <AlertTitle>职位不存在</AlertTitle>
          <AlertDescription>该职位可能已被删除或无权访问</AlertDescription>
        </Alert>
        <Link href="/jobs" className="mt-4 inline-block text-primary underline">
          返回列表
        </Link>
      </div>
    );
  }

  const items = data?.items ?? [];
  // 仅显示待审核的（未改判且通过 AI 筛选）
  const reviewItems = items.filter(
    (item) => !item.manually_overridden && !item.disqualified,
  );

  return (
    <div className="mx-auto max-w-5xl space-y-4 p-6">
      <div className="flex items-center justify-between">
        <div>
          <Link
            href={`/jobs/${jobId}/candidates`}
            className="text-sm text-muted-foreground hover:underline"
          >
            ← 返回候选人列表
          </Link>
          <h1 className="mt-1 text-2xl font-bold">人工评估 · {job.title}</h1>
          <p className="text-sm text-muted-foreground">
            审核 AI 筛选结果，确认进入面试或淘汰
          </p>
        </div>
        <span className="text-sm text-muted-foreground">
          待审核：{reviewItems.length} 人
        </span>
      </div>

      {isError && (
        <Alert variant="destructive">
          <AlertTitle>加载失败</AlertTitle>
          <AlertDescription>
            {(error as Error)?.message ?? "请稍后重试"}
          </AlertDescription>
        </Alert>
      )}

      {isLoading && (
        <div className="py-8 text-sm text-muted-foreground">加载中...</div>
      )}

      {!isLoading && reviewItems.length === 0 && (
        <div className="py-16 text-center text-sm text-muted-foreground">
          暂无待审核的候选人
          <p className="mt-1 text-xs">
            请先触发「筛选流水线」对候选人进行 AI 初筛
          </p>
        </div>
      )}

      {reviewItems.map((item) => {
        const state = actionState[item.id];
        const isActioned = !!state?.result;
        return (
          <div
            key={item.id}
            className="rounded-lg border p-4 transition-colors"
          >
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0 flex-1 space-y-1.5">
                <div className="flex items-center gap-2">
                  <h3 className="font-semibold">{item.name ?? "—"}</h3>
                  <Badge variant="secondary">已通过 AI 筛选</Badge>
                  {isActioned && !state?.loading && (
                    <Badge
                      variant={
                        state?.result?.includes("已通过") ? "success" : "destructive"
                      }
                    >
                      {state?.result}
                    </Badge>
                  )}
                </div>
                {item.screening_reasons && item.screening_reasons.length > 0 && (
                  <div className="text-xs text-muted-foreground">
                    <span className="font-medium">AI 评价：</span>
                    {item.screening_reasons.join("；")}
                  </div>
                )}
              </div>

              {!isActioned && (
                <div className="flex shrink-0 items-center gap-2">
                  <Button
                    size="sm"
                    onClick={() => handleApprove(item)}
                    disabled={state?.loading}
                  >
                    {state?.loading ? "处理中..." : "通过，进入面试"}
                  </Button>
                  <DeleteWithReason
                    onConfirm={(reason) => handleReject(item, reason)}
                    disabled={state?.loading}
                  />
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/** 淘汰按钮 + 理由输入 */
function DeleteWithReason({
  onConfirm,
  disabled,
}: {
  onConfirm: (reason: string) => void;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");

  if (!open) {
    return (
      <Button
        variant="outline"
        size="sm"
        onClick={() => setOpen(true)}
        disabled={disabled}
      >
        淘汰
      </Button>
    );
  }

  return (
    <div className="flex items-center gap-1.5">
      <input
        type="text"
        placeholder="淘汰理由..."
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        className="h-7 w-32 rounded border px-2 text-xs"
        autoFocus
        onKeyDown={(e) => {
          if (e.key === "Enter" && reason.trim()) {
            onConfirm(reason.trim());
            setOpen(false);
          }
          if (e.key === "Escape") setOpen(false);
        }}
      />
      <Button
        variant="destructive"
        size="sm"
        disabled={!reason.trim() || disabled}
        onClick={() => {
          if (reason.trim()) {
            onConfirm(reason.trim());
            setOpen(false);
          }
        }}
      >
        确认
      </Button>
      <Button
        variant="ghost"
        size="sm"
        onClick={() => setOpen(false)}
      >
        取消
      </Button>
    </div>
  );
}

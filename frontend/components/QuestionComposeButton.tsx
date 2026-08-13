"use client";

import { useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import {
  useAssembleQuestions,
  useComposeFromBank,
  useQuestionCategories,
} from "@/hooks/useQuestionBank";

/**
 * 「从题库凑分」按钮 + 确认面板。
 *
 * 流程：点开 → 预览（assemble 用各分类 target_points，展示选中题 + 缺口）→
 * 确认 → compose 写入 session（新 batch）。
 */

interface Props {
  sessionId: string;
}

export function QuestionComposeButton({ sessionId }: Props) {
  const [open, setOpen] = useState(false);
  const { data: categories, isLoading: catLoading } = useQuestionCategories();
  const assemble = useAssembleQuestions();
  const compose = useComposeFromBank(sessionId);

  const activeCats = (categories ?? []).filter((c) => c.is_active && c.target_points > 0);
  const totalTarget = activeCats.reduce((s, c) => s + c.target_points, 0);

  const onPreview = () => {
    assemble.mutate({});
  };

  const onConfirm = async () => {
    try {
      await compose.mutateAsync({});
      setOpen(false);
      assemble.reset();
    } catch {
      // 错误由 mutation state 展示
    }
  };

  const preview = assemble.data;
  const previewError = assemble.error
    ? extractErrorMessage(assemble.error) ?? "预览失败"
    : null;
  const composeError = compose.error
    ? extractErrorMessage(compose.error) ?? "组卷失败"
    : null;

  return (
    <>
      <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
        从题库凑分
      </Button>

      <Dialog
        open={open}
        onClose={() => {
          setOpen(false);
          assemble.reset();
        }}
        title="从题库凑分组卷"
        description={`按各分类目标分配额组卷，目标合计 ${totalTarget} 分`}
        maxWidth="max-w-2xl"
        footer={
          <>
            <Button
              variant="outline"
              onClick={() => {
                setOpen(false);
                assemble.reset();
              }}
              disabled={compose.isPending}
            >
              取消
            </Button>
            {!preview ? (
              <Button onClick={onPreview} disabled={assemble.isPending || catLoading}>
                {assemble.isPending ? "预览中…" : "预览凑题"}
              </Button>
            ) : (
              <Button
                onClick={onConfirm}
                disabled={compose.isPending || preview.items.length === 0}
              >
                {compose.isPending ? "组卷中…" : `确认组卷（${preview.items.length} 题）`}
              </Button>
            )}
          </>
        }
      >
        {/* 分类配额概览 */}
        <div className="space-y-1">
          <p className="text-sm font-medium">分类配额：</p>
          {catLoading ? (
            <p className="text-xs text-muted-foreground">加载分类中…</p>
          ) : activeCats.length === 0 ? (
            <Alert variant="destructive">
              <AlertDescription>
                暂无已启用且目标分大于 0 的分类。请先在「题库管理」配置分类与目标分。
              </AlertDescription>
            </Alert>
          ) : (
            <div className="flex flex-wrap gap-1">
              {activeCats.map((c) => (
                <Badge key={c.id} variant="secondary">
                  {c.name} {c.target_points}
                </Badge>
              ))}
              <Badge variant={totalTarget === 100 ? "success" : "warning"}>
                合计 {totalTarget}
              </Badge>
            </div>
          )}
        </div>

        {/* 预览结果 */}
        {previewError && (
          <Alert variant="destructive">
            <AlertDescription>{previewError}</AlertDescription>
          </Alert>
        )}
        {preview && (
          <div className="space-y-2">
            <div className="flex items-center gap-2 text-sm">
              <span>
                预计 <strong>{preview.items.length}</strong> 题，
              </span>
              <Badge variant={preview.actual_total === preview.target_total ? "success" : "warning"}>
                实际 {preview.actual_total} / 目标 {preview.target_total}
              </Badge>
            </div>
            {preview.deficits.length > 0 && (
              <Alert>
                <AlertDescription>
                  部分分类凑不满：
                  {preview.deficits.map((d) => (
                    <span key={d.category_id} className="ml-1">
                      {d.category_name} 缺 {d.gap} 分；
                    </span>
                  ))}
                  建议补充题库或调整配额。
                </AlertDescription>
              </Alert>
            )}
            <div className="max-h-60 space-y-1 overflow-y-auto rounded-md border p-2 text-xs">
              {preview.items.map((it, idx) => (
                <div key={it.id} className="flex gap-2">
                  <Badge variant="outline" className="shrink-0">
                    {it.points}
                  </Badge>
                  <span className="text-muted-foreground">
                    {idx + 1}. {it.question.slice(0, 60)}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
        {composeError && (
          <Alert variant="destructive">
            <AlertDescription>{composeError}</AlertDescription>
          </Alert>
        )}
      </Dialog>
    </>
  );
}

function extractErrorMessage(err: unknown): string | null {
  const msg = (
    err as { response?: { data?: { error?: { message?: string } } } }
  )?.response?.data?.error?.message;
  return msg ?? null;
}

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
 * 流程：点开 → 预览（dynamic 模式：按候选人简历 + JD 动态匹配配额，展示
 * 匹配信号 + 配额调整 + 选中题 + 缺口）→ 确认 → compose 写入 session（新 batch）。
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
    assemble.mutate({ session_id: sessionId, dynamic: true });
  };

  const onConfirm = async () => {
    try {
      await compose.mutateAsync({ dynamic: true });
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
        description="按候选人简历与 JD 动态匹配配额，目标约 30 题"
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
        {/* 动态匹配信号 */}
        {preview?.plan && (
          <div className="space-y-1">
            <p className="text-sm font-medium">匹配信号（来自简历与 JD）：</p>
            {preview.plan.signals.length === 0 ? (
              <p className="text-xs text-muted-foreground">
                未提取到简历/JD 技能信号，退化为静态配额。
              </p>
            ) : (
              <div className="flex flex-wrap gap-1">
                {preview.plan.signals.slice(0, 12).map((s) => (
                  <Badge key={s.signal} variant={s.weight >= 2 ? "default" : "secondary"}>
                    {s.signal}
                  </Badge>
                ))}
              </div>
            )}
          </div>
        )}

        {/* 配额调整（动态模式：基准 → 实际） */}
        <div className="space-y-1">
          <p className="text-sm font-medium">
            {preview?.plan ? "配额调整（基准 → 匹配后）" : "分类配额"}：
          </p>
          {catLoading ? (
            <p className="text-xs text-muted-foreground">加载分类中…</p>
          ) : activeCats.length === 0 ? (
            <Alert variant="destructive">
              <AlertDescription>
                暂无已启用且目标分大于 0 的分类。请先在「题库管理」配置分类与目标分。
              </AlertDescription>
            </Alert>
          ) : preview?.plan ? (
            <div className="flex flex-wrap gap-1">
              {preview.plan.quotas.map((q) => (
                <Badge
                  key={q.category_id}
                  variant={q.quota_points > q.base_points ? "success" : "secondary"}
                >
                  {q.category_name} {q.base_points}→{q.quota_points}
                </Badge>
              ))}
              <Badge variant="outline">合计 {preview.plan.total_target}</Badge>
            </div>
          ) : (
            <div className="flex flex-wrap gap-1">
              {activeCats.map((c) => (
                <Badge key={c.id} variant="secondary">
                  {c.name} {c.target_points}
                </Badge>
              ))}
              <Badge variant="outline">合计 {totalTarget}</Badge>
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

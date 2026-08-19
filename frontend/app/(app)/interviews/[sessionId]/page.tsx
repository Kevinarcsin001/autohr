"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  type BatchFeedbackItem,
  type HiringRecommendation,
  type InterviewQuestionOut,
  type InterviewSessionStatus,
} from "@/lib/api/interview";
import {
  useBatchSaveFeedback,
  useInterviewSession,
  useUpdateInterviewSession,
} from "@/hooks/useInterviewSession";
import {
  useGenerateRecommendation,
  useHiringRecommendation,
} from "@/hooks/useHiringRecommendation";
import { QuestionComposeButton } from "@/components/QuestionComposeButton";
import { formatDateTime } from "@/lib/utils";

const STATUS_VARIANT: Record<string, "default" | "secondary" | "success" | "warning"> = {
  scheduled: "secondary",
  in_progress: "warning",
  completed: "success",
};

const STATUS_LABEL: Record<string, string> = {
  scheduled: "待面试",
  in_progress: "进行中",
  completed: "已完成",
};

const STATUS_OPTIONS: { value: InterviewSessionStatus; label: string }[] = [
  { value: "scheduled", label: "待面试" },
  { value: "in_progress", label: "进行中" },
  { value: "completed", label: "已完成" },
];

const DIMENSION_LABEL: Record<string, string> = {
  skill: "技能深挖",
  project: "项目经历",
  weakness: "短板验证",
  culture: "文化匹配",
};

const REC_LABEL: Record<HiringRecommendation, string> = {
  hire: "建议录用",
  reserve: "保留",
  reject: "建议淘汰",
};

const REC_VARIANT: Record<HiringRecommendation, "success" | "warning" | "destructive"> = {
  hire: "success",
  reserve: "warning",
  reject: "destructive",
};

export default function InterviewSessionPage() {
  const params = useParams<{ sessionId: string }>();
  const sessionId = params.sessionId;
  const router = useRouter();

  const { data, isLoading, isError, error } = useInterviewSession(sessionId);
  const updateSession = useUpdateInterviewSession(sessionId);
  const batchSave = useBatchSaveFeedback(sessionId);
  const generateRec = useGenerateRecommendation(sessionId);
  const { data: recData } = useHiringRecommendation(sessionId);

  // 本地反馈编辑状态
  const [feedbacks, setFeedbacks] = useState<Record<string, { feedback: string; rating: number | null }>>({});
  const [overallNotes, setOverallNotes] = useState("");
  const [saving, setSaving] = useState(false);

  // 从已有数据初始化本地编辑状态
  useEffect(() => {
    if (!data) return;
    setOverallNotes(data.session.overall_notes ?? "");
    const init: Record<string, { feedback: string; rating: number | null }> = {};
    for (const q of data.questions) {
      init[q.id] = {
        feedback: q.feedback ?? "",
        rating: q.rating ?? null,
      };
    }
    setFeedbacks(init);
  }, [data]);

  const handleSaveFeedback = useCallback(async () => {
    setSaving(true);
    try {
      const items: BatchFeedbackItem[] = Object.entries(feedbacks).map(
        ([questionId, fb]) => ({
          question_id: questionId,
          feedback: fb.feedback || null,
          rating: fb.rating,
        }),
      );
      await batchSave.mutateAsync({ feedbacks: items });
    } finally {
      setSaving(false);
    }
  }, [feedbacks, batchSave]);

  const handleStatusChange = useCallback(
    (status: InterviewSessionStatus) => {
      updateSession.mutate({ status });
    },
    [updateSession],
  );

  const handleSaveNotes = useCallback(() => {
    updateSession.mutate({ overall_notes: overallNotes || undefined });
  }, [overallNotes, updateSession]);

  if (isLoading) {
    return <div className="p-8 text-sm">加载中...</div>;
  }

  if (isError || !data) {
    return (
      <div className="p-8">
        <Alert variant="destructive">
          <AlertTitle>加载失败</AlertTitle>
          <AlertDescription>
            {(error as Error)?.message ?? "会话不存在或无权访问"}
          </AlertDescription>
        </Alert>
        <Link href="/interviews" className="mt-4 inline-block text-primary underline">
          返回列表
        </Link>
      </div>
    );
  }

  const { session, candidate_name, candidate_email, job_title, job_jd_summary, questions } = data;
  const recommendation = recData?.recommendation ?? data.recommendation ?? null;

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-6">
      {/* 顶部：返回 + 候选人/职位信息 */}
      <header className="space-y-2">
        <div className="flex items-center justify-between">
          <Link href="/interviews" className="text-sm text-muted-foreground hover:underline">
            ← 返回面试列表
          </Link>
          <Link
            href={`/interviews/${sessionId}/adaptive`}
            className="text-sm text-primary hover:underline"
          >
            渐进式自适应面试 →
          </Link>
        </div>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="space-y-1">
            <h1 className="text-xl font-bold">{candidate_name ?? "—"}</h1>
            <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
              {candidate_email && <span>{candidate_email}</span>}
              <span>·</span>
              <span>职位：{job_title ?? "—"}</span>
              <span>·</span>
              <span>创建：{session.created_at ? formatDateTime(session.created_at) : "—"}</span>
            </div>
            {job_jd_summary && (
              <p className="max-w-2xl text-xs text-muted-foreground">
                JD：{job_jd_summary}
              </p>
            )}
          </div>

          <div className="flex items-center gap-2">
            <Badge variant={STATUS_VARIANT[session.status] ?? "default"}>
              {STATUS_LABEL[session.status] ?? session.status}
            </Badge>
          </div>
        </div>
      </header>

      {/* 状态切换 */}
      <div className="flex items-center gap-2 rounded-lg border bg-muted/30 p-3">
        <span className="text-sm font-medium">面试状态：</span>
        {STATUS_OPTIONS.map((opt) => (
          <Button
            key={opt.value}
            variant={session.status === opt.value ? "default" : "outline"}
            size="sm"
            onClick={() => handleStatusChange(opt.value)}
            disabled={updateSession.isPending}
          >
            {opt.label}
          </Button>
        ))}
      </div>

      {/* 面试题目列表 */}
      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold">面试题目（{questions.length} 题）</h2>
          <QuestionComposeButton sessionId={sessionId} />
        </div>

        {questions.length === 0 ? (
          <Alert>
            <AlertDescription>该会话暂无面试题，请先生成面试问题。</AlertDescription>
          </Alert>
        ) : (
          questions.map((q) => (
            <QuestionFeedbackCard
              key={q.id}
              question={q}
              feedback={feedbacks[q.id] ?? { feedback: "", rating: null }}
              onChange={(next) =>
                setFeedbacks((prev) => ({ ...prev, [q.id]: next }))
              }
            />
          ))
        )}

        {questions.length > 0 && (
          <div className="flex justify-end">
            <Button onClick={handleSaveFeedback} disabled={saving || batchSave.isPending}>
              {saving || batchSave.isPending ? "保存中..." : "保存所有反馈"}
            </Button>
          </div>
        )}

        {batchSave.isError && (
          <Alert variant="destructive">
            <AlertDescription>
              {(batchSave.error as Error)?.message ?? "保存失败"}
            </AlertDescription>
          </Alert>
        )}

        {batchSave.isSuccess && batchSave.data && (
          <Alert>
            <AlertDescription>
              已保存 {batchSave.data.saved} 条反馈
              {batchSave.data.errors.length > 0 &&
                `，${batchSave.data.errors.length} 条失败`}
            </AlertDescription>
          </Alert>
        )}
      </section>

      {/* 整体评价 */}
      <section className="space-y-2 rounded-lg border p-4">
        <h2 className="text-lg font-semibold">整体评价</h2>
        <textarea
          placeholder="面试官整体评价..."
          value={overallNotes}
          onChange={(e) => setOverallNotes(e.target.value)}
          rows={4}
          className="w-full rounded-md border px-3 py-2 text-sm"
        />
        <div className="flex justify-end">
          <Button
            variant="outline"
            size="sm"
            onClick={handleSaveNotes}
            disabled={updateSession.isPending}
          >
            保存评价
          </Button>
        </div>
      </section>

      {/* AI 录用建议 */}
      <section className="space-y-3 rounded-lg border p-4">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold">AI 录用建议</h2>
          {!recommendation && (
            <Button
              onClick={() => generateRec.mutate()}
              disabled={generateRec.isPending}
              variant="outline"
            >
              {generateRec.isPending ? "生成中..." : "生成录用建议"}
            </Button>
          )}
        </div>

        {generateRec.isError && (
          <Alert variant="destructive">
            <AlertDescription>
              {(generateRec.error as Error)?.message ?? "生成失败"}
            </AlertDescription>
          </Alert>
        )}

        {recommendation && (
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <Badge variant={REC_VARIANT[recommendation.recommendation]}>
                {REC_LABEL[recommendation.recommendation]}
              </Badge>
              {recommendation.generated_by && (
                <span className="text-xs text-muted-foreground">
                  由 {recommendation.generated_by} 生成
                </span>
              )}
            </div>

            {recommendation.reasons && recommendation.reasons.length > 0 && (
              <div>
                <h3 className="mb-1 text-sm font-medium">核心理由</h3>
                <ul className="list-inside list-disc space-y-1 text-sm text-muted-foreground">
                  {recommendation.reasons.map((r, i) => (
                    <li key={i}>{r}</li>
                  ))}
                </ul>
              </div>
            )}

            {recommendation.risks && recommendation.risks.length > 0 && (
              <div>
                <h3 className="mb-1 text-sm font-medium text-amber-600">潜在风险</h3>
                <ul className="list-inside list-disc space-y-1 text-sm text-muted-foreground">
                  {recommendation.risks.map((r, i) => (
                    <li key={i}>{r}</li>
                  ))}
                </ul>
              </div>
            )}

            {recommendation.probation_focus && recommendation.probation_focus.length > 0 && (
              <div>
                <h3 className="mb-1 text-sm font-medium text-blue-600">试用期关注点</h3>
                <ul className="list-inside list-disc space-y-1 text-sm text-muted-foreground">
                  {recommendation.probation_focus.map((r, i) => (
                    <li key={i}>{r}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </section>
    </div>
  );
}

/** 单个题目的反馈卡片 */
function QuestionFeedbackCard({
  question,
  feedback,
  onChange,
}: {
  question: InterviewQuestionOut;
  feedback: { feedback: string; rating: number | null };
  onChange: (next: { feedback: string; rating: number | null }) => void;
}) {
  const [expanded, setExpanded] = useState(false);

  const hasContent = !!feedback.feedback || feedback.rating !== null;

  return (
    <div className="rounded-lg border p-4">
      <div className="flex items-start gap-3">
        <Badge variant="outline" className="mt-0.5 shrink-0 text-xs">
          {DIMENSION_LABEL[question.dimension] ?? question.dimension}
        </Badge>
        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-2">
            <p className="text-sm leading-relaxed">{question.question}</p>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setExpanded(!expanded)}
              className="shrink-0 text-xs"
            >
              {expanded ? "收起" : hasContent ? "已填写" : "填写反馈"}
            </Button>
          </div>

          {!expanded && hasContent && (
            <div className="mt-1 text-xs text-muted-foreground">
              {feedback.rating !== null && <span>评分：{feedback.rating}/5</span>}
              {feedback.feedback && (
                <span className="ml-2 truncate block">
                  反馈：{feedback.feedback.slice(0, 60)}
                  {feedback.feedback.length > 60 ? "..." : ""}
                </span>
              )}
            </div>
          )}

          {expanded && (
            <div className="mt-3 space-y-2">
              <textarea
                className="w-full rounded-md border px-3 py-2 text-sm"
                rows={3}
                placeholder="面试官反馈..."
                maxLength={2000}
                value={feedback.feedback}
                onChange={(e) =>
                  onChange({ ...feedback, feedback: e.target.value })
                }
              />
              <div className="flex items-center gap-2">
                <span className="text-xs text-muted-foreground">评分：</span>
                {[1, 2, 3, 4, 5].map((rating) => (
                  <Button
                    key={rating}
                    variant={
                      feedback.rating === rating ? "default" : "outline"
                    }
                    size="sm"
                    className="h-7 w-7 p-0 text-xs"
                    onClick={() =>
                      onChange({
                        ...feedback,
                        rating: feedback.rating === rating ? null : rating,
                      })
                    }
                  >
                    {rating}
                  </Button>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

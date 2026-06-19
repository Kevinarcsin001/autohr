"use client";

import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useInterviewQuestions } from "@/hooks/useInterviewQuestions";
import { useSubmitFeedback } from "@/hooks/useSubmitFeedback";
import { cn } from "@/lib/utils";

/**
 * 面试问题列表 + 反馈输入（任务 24）。
 *
 * 行为：
 * - 列出最新 batch 的 5-8 题（按 sort_order）
 * - 每题可折叠"反馈输入"（feedback textarea + 1-5 评分）
 * - 提交 → useSubmitFeedback 触发；成功后关闭折叠
 */

interface InterviewQuestionsProps {
  candidateId: string;
  jobId: string;
  className?: string;
}

const DIMENSION_LABEL: Record<string, string> = {
  skill: "技能深挖",
  project: "项目经历",
  weakness: "短板验证",
  culture: "文化匹配",
};

const DIMENSION_VARIANT: Record<
  string,
  "default" | "secondary" | "success" | "warning" | "destructive" | "outline"
> = {
  skill: "default",
  project: "secondary",
  weakness: "warning",
  culture: "outline",
};

export function InterviewQuestions({
  candidateId,
  jobId,
  className,
}: InterviewQuestionsProps) {
  const { data, isLoading, isError } = useInterviewQuestions(
    candidateId,
    jobId,
  );

  if (isLoading) {
    return (
      <Card className={className}>
        <CardHeader>
          <CardTitle className="text-base">面试问题</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">加载中...</p>
        </CardContent>
      </Card>
    );
  }

  if (isError || !data || data.items.length === 0) {
    return (
      <Card className={className}>
        <CardHeader>
          <CardTitle className="text-base">面试问题</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            尚未生成面试问题（需先完成评分）
          </p>
        </CardContent>
      </Card>
    );
  }

  const sorted = [...data.items].sort((a, b) => a.sort_order - b.sort_order);

  return (
    <Card className={className}>
      <CardHeader>
        <CardTitle className="text-base">
          面试问题
          <span className="ml-2 text-sm font-normal text-muted-foreground">
            共 {data.items.length} 题
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        {sorted.map((q) => (
          <QuestionItem
            key={q.id}
            question={q}
            candidateId={candidateId}
            jobId={jobId}
          />
        ))}
      </CardContent>
    </Card>
  );
}

function QuestionItem({
  question,
  candidateId,
  jobId,
}: {
  question: ReturnType<typeof useInterviewQuestions>["data"] extends infer T
    ? T extends { items: infer I } | undefined
      ? I extends Array<infer U>
        ? U
        : never
      : never
    : never;
  candidateId: string;
  jobId: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const [feedback, setFeedback] = useState(question.feedback ?? "");
  const [rating, setRating] = useState<number | null>(
    question.rating ?? null,
  );

  const submit = useSubmitFeedback(candidateId, jobId, {
    onSuccess: () => setExpanded(false),
  });

  return (
    <div className="rounded-md border p-3">
      <div className="flex items-start gap-3">
        <Badge variant={DIMENSION_VARIANT[question.dimension] ?? "secondary"}>
          {DIMENSION_LABEL[question.dimension] ?? question.dimension}
        </Badge>
        <div className="flex-1">
          <p className="text-sm">{question.question}</p>
          {(question.feedback || question.rating) && !expanded && (
            <p className="mt-1 text-xs text-muted-foreground">
              已记录反馈
              {question.rating ? ` · 评分 ${question.rating}` : ""}
            </p>
          )}
        </div>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
        >
          {expanded ? "收起" : question.feedback ? "编辑反馈" : "添加反馈"}
        </Button>
      </div>

      {expanded && (
        <div className="mt-3 space-y-3 border-t pt-3">
          <div>
            <Label htmlFor={`fb-${question.id}`} className="text-xs">
              反馈（最多 2000 字）
            </Label>
            <Input
              id={`fb-${question.id}`}
              value={feedback}
              onChange={(e) => setFeedback(e.target.value)}
              placeholder="记录面试官的观察 / 候选人回答要点"
              maxLength={2000}
              className="mt-1"
            />
          </div>
          <div className="flex items-center gap-2">
            <Label className="text-xs">评分</Label>
            <div className="flex gap-1">
              {[1, 2, 3, 4, 5].map((n) => (
                <button
                  key={n}
                  type="button"
                  onClick={() => setRating(n === rating ? null : n)}
                  className={cn(
                    "size-8 rounded border text-sm",
                    rating === n
                      ? "border-primary bg-primary/10 text-primary"
                      : "hover:bg-accent",
                  )}
                >
                  {n}
                </button>
              ))}
            </div>
          </div>
          <div className="flex justify-end gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setExpanded(false)}
            >
              取消
            </Button>
            <Button
              size="sm"
              disabled={submit.isPending}
              onClick={() =>
                submit.mutate({
                  questionId: question.id,
                  payload: {
                    feedback: feedback.trim() || null,
                    rating,
                  },
                })
              }
            >
              {submit.isPending ? "保存中..." : "保存反馈"}
            </Button>
          </div>
          {submit.isError && (
            <p className="text-xs text-destructive">
              提交失败：{(submit.error as Error)?.message ?? "未知错误"}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

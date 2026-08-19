"use client";

/**
 * 渐进式自适应面试工作台（M1）。
 *
 * 布局三栏：
 * - 左：分支进度（亲和度排序；状态 pending/active/done/weak + 平均分）
 * - 中：当前题卡（题面/答题输入/评分结果含证据）+ 下一题按钮（含选题理由）
 * - 右：能力画像 + 回合时间线（逐题评分与决策）
 *
 * 数据流：state 查询为唯一真源；start/answer/next 操作后 invalidate 自动刷新。
 */
import { useCallback, useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  useAdaptiveAnswer,
  useAdaptiveNext,
  useAdaptiveStart,
  useAdaptiveState,
} from "@/hooks/useAdaptiveInterview";
import {
  ADAPTIVE_STATUS_LABEL,
  ADAPTIVE_STATUS_VARIANT,
  DECISION_LABEL,
} from "@/lib/constants/adaptive";

const RATING_VARIANT = (r: number | null) =>
  r === null
    ? "outline"
    : r >= 4
      ? "success"
      : r === 3
        ? "secondary"
        : "destructive";

export default function AdaptiveInterviewPage() {
  const params = useParams<{ sessionId: string }>();
  const sessionId = params.sessionId;

  const start = useAdaptiveStart(sessionId);
  const { data, isLoading, isError, error } = useAdaptiveState(sessionId, !start.isIdle);
  const answer = useAdaptiveAnswer(sessionId);
  const next = useAdaptiveNext(sessionId);

  const [answerText, setAnswerText] = useState("");
  const [nextResult, setNextResult] = useState<{
    reason?: string;
    done?: boolean;
    doneReason?: string | null;
  } | null>(null);
  const startError = start.error as unknown as { response?: { data?: { error?: { message?: string } } } } | null;
  const startErrorMsg = startError?.response?.data?.error?.message ?? null;

  // 当前待答题 = 最后一个未回答的回合
  const currentTurn = useMemo(
    () => (data ? (data.turns.find((t) => t.answer_text === null) ?? null) : null),
    [data],
  );
  const lastTurn = data?.turns[data.turns.length - 1] ?? null;

  const onStart = useCallback(() => {
    setNextResult(null);
    start.mutate();
  }, [start]);

  const onSubmitAnswer = useCallback(() => {
    if (!currentTurn || !answerText.trim()) return;
    answer.mutate(
      { turn_id: currentTurn.id, answer_text: answerText },
      {
        onSuccess: () => setAnswerText(""),
      },
    );
  }, [answer, answerText, currentTurn]);

  const onNext = useCallback(() => {
    next.mutate(undefined, {
      onSuccess: (res) =>
        setNextResult({
          reason: res.decision?.reason,
          done: res.done,
          doneReason: res.done_reason,
        }),
    });
  }, [next]);

  // ------------------------------------------------------------------ 启动前
  if (start.isIdle && !data) {
    return (
      <div className="mx-auto max-w-2xl space-y-4 p-6">
        <h1 className="text-xl font-semibold">渐进式自适应面试</h1>
        <Alert>
          <AlertDescription>
            系统将根据候选人简历与 JD 匹配分支，从最相关分支开始提问；
            每答一题自动评分（对照题库参考答案），答得好同分支加深、答得差换下一分支。
          </AlertDescription>
        </Alert>
        <Button onClick={onStart} disabled={start.isPending}>
          {start.isPending ? "正在生成分支计划…" : "开始自适应面试"}
        </Button>
        {startErrorMsg && (
          <Alert variant="destructive">
            <AlertDescription>{startErrorMsg}</AlertDescription>
          </Alert>
        )}
        <p className="text-xs text-muted-foreground">
          <Link href={`/interviews/${sessionId}`} className="underline">
            ← 返回会话详情
          </Link>
        </p>
      </div>
    );
  }

  if (isLoading) return <div className="p-6 text-sm text-muted-foreground">加载中…</div>;
  if (isError || !data) {
    return (
      <div className="p-6">
        <Alert variant="destructive">
          <AlertDescription>{extractErr(error) ?? "加载失败"}</AlertDescription>
        </Alert>
      </div>
    );
  }

  const canAnswer = !!currentTurn && !answer.isPending;
  const showNextBtn = !currentTurn && !!lastTurn?.rating && !data.done;

  return (
    <div className="space-y-4 p-6">
      {/* 顶栏 */}
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="text-lg font-semibold">自适应面试</h1>
        <Badge variant="outline">{data.total_turns} 回合</Badge>
        <Badge variant="outline">已答 {data.answered_turns}</Badge>
        {data.plan_signals.slice(0, 6).map((s) => (
          <Badge key={s.signal} variant={s.weight >= 2 ? "default" : "secondary"}>
            {s.signal}
          </Badge>
        ))}
        <span className="ml-auto text-xs">
          <Link href={`/interviews/${sessionId}`} className="underline text-muted-foreground">
            会话详情 →
          </Link>
        </span>
      </div>

      <div className="grid gap-4 lg:grid-cols-[260px_1fr_280px]">
        {/* 左：分支进度 */}
        <div className="space-y-2">
          <p className="text-sm font-medium">分支进度</p>
          <div className="space-y-1.5 rounded-md border p-2">
            {data.branches.map((b) => (
              <div key={b.category_id} className="flex items-center gap-2 text-sm">
                <span className="min-w-0 flex-1 truncate" title={b.category_name}>
                  {b.category_name}
                </span>
                <Badge variant={ADAPTIVE_STATUS_VARIANT[b.status] ?? "outline"}>
                  {ADAPTIVE_STATUS_LABEL[b.status] ?? b.status}
                </Badge>
                {b.avg_rating !== null && (
                  <Badge variant={RATING_VARIANT(Math.round(b.avg_rating))}>
                    {b.avg_rating.toFixed(1)}
                  </Badge>
                )}
              </div>
            ))}
          </div>
          {/* 能力画像 */}
          <p className="pt-1 text-sm font-medium">能力画像</p>
          <div className="space-y-1.5 rounded-md border p-2">
            {Object.entries(data.ability).length === 0 ? (
              <p className="text-xs text-muted-foreground">暂无评分数据</p>
            ) : (
              Object.entries(data.ability).map(([name, v]) => (
                <div key={name} className="text-sm">
                  <div className="flex justify-between text-xs">
                    <span className="truncate" title={name}>
                      {name}
                    </span>
                    <span className="text-muted-foreground">{Math.round(v * 100)}%</span>
                  </div>
                  <div className="h-1.5 overflow-hidden rounded bg-muted">
                    <div
                      className={`h-full ${v >= 0.7 ? "bg-green-500" : v >= 0.4 ? "bg-yellow-500" : "bg-red-500"}`}
                      style={{ width: `${Math.min(100, Math.round(v * 100))}%` }}
                    />
                  </div>
                </div>
              ))
            )}
          </div>
        </div>

        {/* 中：当前题 + 操作 */}
        <div className="space-y-3">
          {data.done ? (
            <Alert>
              <AlertDescription>
                ✅ 面试完成 — {data.done_reason ?? "全部回合结束"}。
                回合记录与评分已保存，可回到会话详情查看录用建议。
              </AlertDescription>
            </Alert>
          ) : currentTurn ? (
            <>
              <div className="rounded-md border p-4">
                <div className="mb-2 flex items-center gap-2 text-xs text-muted-foreground">
                  <Badge variant="outline">第 {currentTurn.seq} 题</Badge>
                  {currentTurn.category_name && (
                    <Badge variant="secondary">{currentTurn.category_name}</Badge>
                  )}
                  {nextResult?.reason && <span>选题理由：{nextResult.reason}</span>}
                </div>
                <p className="whitespace-pre-wrap text-[15px] leading-relaxed">
                  {currentTurn.question_text}
                </p>
              </div>
              <textarea
                className="min-h-[120px] w-full rounded-md border p-3 text-sm"
                placeholder="记录候选人回答（M1 手动输入；M2 将接入音频自动转写）…"
                value={answerText}
                onChange={(e) => setAnswerText(e.target.value)}
                disabled={answer.isPending}
              />
              {answer.error && (
                <Alert variant="destructive">
                  <AlertDescription>{extractErr(answer.error) ?? "提交失败"}</AlertDescription>
                </Alert>
              )}
              <div className="flex items-center gap-2">
                <Button onClick={onSubmitAnswer} disabled={!canAnswer || !answerText.trim()}>
                  {answer.isPending ? "评分中…" : "提交回答并评分"}
                </Button>
                {lastTurn?.rating_evidence?.follow_up_suggestion && (
                  <span className="text-xs text-muted-foreground">
                    上题建议：{lastTurn.rating_evidence.follow_up_suggestion.slice(0, 80)}
                  </span>
                )}
              </div>
            </>
          ) : (
            <div className="space-y-3">
              {/* 刚评分完，等待下一题 */}
              {lastTurn && <LastRatingCard turn={lastTurn} />}
              {showNextBtn && (
                <Button onClick={onNext} disabled={next.isPending}>
                  {next.isPending ? "选择下一题…" : "下一题 →"}
                </Button>
              )}
              {nextResult?.done && (
                <Alert>
                  <AlertDescription>✅ {nextResult.doneReason ?? "面试完成"}</AlertDescription>
                </Alert>
              )}
            </div>
          )}
        </div>

        {/* 右：时间线 */}
        <div className="space-y-2">
          <p className="text-sm font-medium">回合时间线</p>
          <div className="max-h-[70vh] space-y-1.5 overflow-y-auto rounded-md border p-2">
            {data.turns.length === 0 && (
              <p className="text-xs text-muted-foreground">尚未开始</p>
            )}
            {[...data.turns].reverse().map((t) => (
              <div key={t.id} className="rounded border p-2 text-xs">
                <div className="flex items-center gap-1.5">
                  <span className="text-muted-foreground">#{t.seq}</span>
                  <span className="min-w-0 flex-1 truncate" title={t.question_text}>
                    {t.question_text}
                  </span>
                  <Badge variant={RATING_VARIANT(t.rating)}>
                    {t.rating ?? "待评"}
                  </Badge>
                </div>
                {t.next_decision?.action && (
                  <p className="mt-1 text-muted-foreground">
                    → {DECISION_LABEL[t.next_decision.action] ?? t.next_decision.action}
                    {t.next_decision.reason ? `：${t.next_decision.reason.slice(0, 50)}` : ""}
                  </p>
                )}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

/** 上一题评分证据卡（要点命中/遗漏/亮点/问题）。 */
function LastRatingCard({ turn }: { turn: NonNullable<ReturnType<typeof useAdaptiveState>["data"]>["turns"][number] }) {
  const ev = turn.rating_evidence;
  if (!ev && turn.rating == null) return null;
  return (
    <div className="rounded-md border p-4 text-sm">
      <div className="mb-2 flex items-center gap-2">
        <Badge variant={RATING_VARIANT(turn.rating)}>
          评分 {turn.rating ?? "—"} / 5
        </Badge>
        {turn.rating_model && (
          <span className="text-xs text-muted-foreground">{turn.rating_model}</span>
        )}
      </div>
      {ev && (
        <div className="space-y-1.5 text-xs">
          {ev.key_points_hit && ev.key_points_hit.length > 0 && (
            <p className="text-green-700 dark:text-green-400">
              ✓ 命中要点：{ev.key_points_hit.join("；")}
            </p>
          )}
          {ev.key_points_missed && ev.key_points_missed.length > 0 && (
            <p className="text-amber-700 dark:text-amber-400">
              ⚠ 遗漏要点：{ev.key_points_missed.join("；")}
            </p>
          )}
          {ev.flaws && ev.flaws.length > 0 && (
            <p className="text-red-700 dark:text-red-400">✗ 问题：{ev.flaws.join("；")}</p>
          )}
        </div>
      )}
    </div>
  );
}

function extractErr(err: unknown): string | null {
  const msg = (
    err as { response?: { data?: { error?: { message?: string } } } }
  )?.response?.data?.error?.message;
  return msg ?? null;
}

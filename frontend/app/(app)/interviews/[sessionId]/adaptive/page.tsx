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
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ContinuousRecorder } from "@/components/ContinuousRecorder";
import {
  useAdaptiveAnswer,
  useAdaptiveAudio,
  useAdaptiveNext,
  useAdaptiveStart,
  useAdaptiveState,
  useRecordingReplay,
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
  const audio = useAdaptiveAudio(sessionId);
  const next = useAdaptiveNext(sessionId);
  const replay = useRecordingReplay(sessionId);

  const [answerText, setAnswerText] = useState("");
  const [autoMode, setAutoMode] = useState(true);
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
  const currentTurnRef = useRef<typeof currentTurn>(null);
  useEffect(() => {
    currentTurnRef.current = currentTurn;
  }, [currentTurn]);

  /** 自动模式：切片 → 作为当前题回答上传 → 评分落地后自动取下一题 */
  const onAutoSegment = useCallback(
    (blob: Blob, filename: string) => {
      const turn = currentTurnRef.current;
      if (!turn) return;
      audio.mutate(
        { turn_id: turn.id, audio: blob, filename },
        {
          onSuccess: () => {
            // 转写+评分后台异步（约 20s）；稍等后自动取下一题
            setTimeout(
              () =>
                next.mutate(undefined, {
                  onSuccess: (res) =>
                    setNextResult({
                      reason: res.decision?.reason,
                      done: res.done,
                      doneReason: res.done_reason,
                    }),
                }),
              25_000,
            );
          },
        },
      );
    },
    [audio, next],
  );

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

  const onNext = useCallback(
    (opts?: { forceCategoryId?: string; skipCurrent?: boolean }) => {
      next.mutate(opts, {
        onSuccess: (res) =>
          setNextResult({
            reason: res.decision?.reason,
            done: res.done,
            doneReason: res.done_reason,
          }),
      });
    },
    [next],
  );

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
        {/* 左：分支进度（可点击 = 面试官指定分支） */}
        <div className="space-y-2">
          <p className="text-sm font-medium">分支进度</p>
          <div className="space-y-1.5 rounded-md border p-2">
            {data.branches.map((b) => {
              const isActive = currentTurn?.category_id === b.category_id;
              return (
                <div key={b.category_id} className="flex items-center gap-1.5 text-sm">
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
                  <Button
                    size="sm"
                    variant="ghost"
                    className="h-6 px-1.5 text-[11px]"
                    disabled={next.isPending || isActive}
                    title={isActive ? "当前分支" : `从「${b.category_name}」出下一题`}
                    onClick={() => onNext({ forceCategoryId: b.category_id })}
                  >
                    {isActive ? "●" : "问"}
                  </Button>
                </div>
              );
            })}
          </div>
          <p className="text-[11px] text-muted-foreground">
            点击分支「问」= 强制从该分支出题（覆盖自动决策，decision 记录 override）
          </p>
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
            <div className="space-y-3">
              <Alert>
                <AlertDescription>
                  ✅ 面试完成 — {data.done_reason ?? "全部回合结束"}。
                  回合记录与评分已保存，可回到会话详情查看录用建议。
                </AlertDescription>
              </Alert>
              <p className="text-xs text-muted-foreground">
                想继续追问？点击左侧任意分支的「问」可重新出题。
              </p>
            </div>
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
                className="min-h-[100px] w-full rounded-md border p-3 text-sm"
                placeholder="记录候选人回答（手输，或用下方录音自动转写）…"
                value={answerText}
                onChange={(e) => setAnswerText(e.target.value)}
                disabled={answer.isPending}
              />
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <button className="underline" onClick={() => setAutoMode((v) => !v)}>
                  {autoMode ? "切到手动模式(每题点击)" : "切到自动模式(连续监听)"}
                </button>
              </div>
              {autoMode ? (
                <ContinuousRecorder onSegment={onAutoSegment} uploadPending={audio.isPending} />
              ) : (
                <AudioRecorder
                  disabled={
                    !!currentTurn.transcription_status &&
                    currentTurn.transcription_status !== "failed"
                  }
                  uploading={audio.isPending}
                  onUpload={(blob, filename) =>
                    currentTurn &&
                    audio.mutate({ turn_id: currentTurn.id, audio: blob, filename })
                  }
                />
              )}
              {answer.error && (
                <Alert variant="destructive">
                  <AlertDescription>{extractErr(answer.error) ?? "提交失败"}</AlertDescription>
                </Alert>
              )}
              <div className="flex items-center gap-2">
                <Button onClick={onSubmitAnswer} disabled={!canAnswer || !answerText.trim()}>
                  {answer.isPending ? "评分中…" : "提交回答并评分"}
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={next.isPending}
                  title="不满意这题？同分支换考点重新出题（原题作废标记 skipped）"
                  onClick={() => onNext({ skipCurrent: true })}
                >
                  ⟳ 换一题
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
                <Button onClick={() => onNext()} disabled={next.isPending}>
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

        {/* 右：时间线 + 会后回捞 */}
        <div className="space-y-2">
          <p className="text-sm font-medium">回合时间线</p>
          <ReplayPanel
            turns={data.turns}
            recordingStatus={null}
            replay={replay}
          />
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
        {turn.transcription_status === "done" && (
          <Badge variant="outline">音频已转写</Badge>
        )}
        {turn.transcription_status === "failed" && (
          <Badge variant="destructive">转写失败(可重传)</Badge>
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

/**
 * 音频录制器（M2a）：选设备（物理麦=面试官/虚拟声卡=候选人会议声）→ 录 → 上传。
 * 转写异步：上传后状态轮询由 useAdaptiveState 的 invalidate 驱动。
 */
function AudioRecorder({
  disabled,
  uploading,
  onUpload,
}: {
  disabled: boolean;
  uploading: boolean;
  onUpload: (blob: Blob, filename: string) => void;
}) {
  const [devices, setDevices] = useState<MediaDeviceInfo[]>([]);
  const [deviceId, setDeviceId] = useState<string>("");
  const [recording, setRecording] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const mediaRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (disabled) return;
    navigator.mediaDevices
      ?.enumerateDevices()
      .then((all) =>
        setDevices(all.filter((d) => d.kind === "audioinput" && d.deviceId)),
      )
      .catch(() => setDevices([]));
  }, [disabled]);

  const startRec = async () => {
    setError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: deviceId ? { deviceId: { exact: deviceId } } : true,
      });
      const rec = new MediaRecorder(stream, {
        mimeType: MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
          ? "audio/webm;codecs=opus"
          : "audio/webm",
      });
      chunksRef.current = [];
      rec.ondataavailable = (e) => e.data.size > 0 && chunksRef.current.push(e.data);
      rec.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        const blob = new Blob(chunksRef.current, { type: "audio/webm" });
        if (blob.size > 0) onUpload(blob, `turn-${Date.now()}.webm`);
      };
      rec.start(1000);
      mediaRef.current = rec;
      setRecording(true);
      setElapsed(0);
      timerRef.current = setInterval(() => setElapsed((s) => s + 1), 1000);
    } catch {
      setError("无法访问麦克风：请检查浏览器权限；录会议声需先装虚拟声卡（BlackHole/VB-Cable）");
    }
  };

  const stopRec = () => {
    mediaRef.current?.stop();
    mediaRef.current = null;
    setRecording(false);
    if (timerRef.current) clearInterval(timerRef.current);
  };

  useEffect(() => () => {
    if (timerRef.current) clearInterval(timerRef.current);
    mediaRef.current?.stream.getTracks().forEach((t) => t.stop());
  }, []);

  return (
    <div className="space-y-2 rounded-md border p-3">
      <div className="flex flex-wrap items-center gap-2">
        <select
          className="min-w-0 flex-1 rounded border bg-background px-2 py-1 text-xs"
          value={deviceId}
          onChange={(e) => setDeviceId(e.target.value)}
          disabled={disabled || recording}
        >
          <option value="">🎤 默认麦克风（面试官声音）</option>
          {devices.map((d) => (
            <option key={d.deviceId} value={d.deviceId}>
              {d.label || `设备 ${d.deviceId.slice(0, 8)}`}
            </option>
          ))}
        </select>
        {!recording ? (
          <Button variant="outline" size="sm" onClick={startRec} disabled={disabled || uploading}>
            {uploading ? "上传中…" : "● 开始录音"}
          </Button>
        ) : (
          <Button variant="destructive" size="sm" onClick={stopRec}>
            ■ 停止并上传 ({elapsed}s)
          </Button>
        )}
      </div>
      <p className="text-[11px] leading-relaxed text-muted-foreground">
        提示：选「默认麦克风」录面试官提问；录候选人/会议声请在系统中把虚拟声卡
        （BlackHole / VB-Cable）设为输入后在此选择。录音上传后自动转写并评分。
      </p>
      {error && <p className="text-xs text-destructive">{error}</p>}
    </div>
  );
}

/**
 * 会后回捞面板（M2b）：上传整场录制 + 逐题打点(mm:ss) + 触发处理。
 * 场景：忘了开录音/设备故障时，用钉钉/腾讯会议云录制文件事后补齐账本。
 */
function ReplayPanel({
  turns,
  recordingStatus,
  replay,
}: {
  turns: NonNullable<ReturnType<typeof useAdaptiveState>["data"]>["turns"];
  recordingStatus: string | null;
  replay: ReturnType<typeof useRecordingReplay>;
}) {
  const [open, setOpen] = useState(false);
  const [offsets, setOffsets] = useState<Record<string, string>>({});
  const [file, setFile] = useState<File | null>(null);
  const [uploaded, setUploaded] = useState(false);

  const pendingTurns = turns.filter(
    (t) => t.answer_text === null && (t as { audio_start_ms?: number | null }).audio_start_ms == null,
  );

  if (!open) {
    return (
      <button
        className="text-xs text-muted-foreground underline"
        onClick={() => setOpen(true)}
      >
        + 会后回捞（上传会议录制补齐回答）
      </button>
    );
  }

  return (
    <div className="space-y-2 rounded-md border p-3 text-xs">
      <div className="flex items-center justify-between">
        <span className="font-medium">会后回捞</span>
        <button className="text-muted-foreground" onClick={() => setOpen(false)}>
          收起
        </button>
      </div>

      {/* 1. 上传录制 */}
      <div className="flex items-center gap-2">
        <input
          type="file"
          accept="audio/*,video/*"
          className="min-w-0 flex-1 text-xs"
          onChange={(e) => {
            setFile(e.target.files?.[0] ?? null);
            setUploaded(false);
          }}
        />
        <Button
          size="sm"
          variant="outline"
          disabled={!file || replay.upload.isPending || uploaded}
          onClick={() =>
            file &&
            replay.upload.mutate(file, { onSuccess: () => setUploaded(true) })
          }
        >
          {replay.upload.isPending ? "上传中…" : uploaded ? "已上传 ✓" : "上传录制"}
        </Button>
      </div>

      {/* 2. 逐题打点 */}
      <p className="text-muted-foreground">
        听回放标注每题起始时间（格式 mm:ss，如 12:30）；未标注的题会被跳过。
      </p>
      <div className="max-h-40 space-y-1 overflow-y-auto">
        {pendingTurns.length === 0 && (
          <p className="text-muted-foreground">全部题目已有回答或已打点。</p>
        )}
        {pendingTurns.map((t) => (
          <div key={t.id} className="flex items-center gap-2">
            <span className="w-6 text-muted-foreground">#{t.seq}</span>
            <span className="min-w-0 flex-1 truncate" title={t.question_text}>
              {t.question_text}
            </span>
            <input
              className="w-20 rounded border bg-background px-2 py-0.5"
              placeholder="mm:ss"
              value={offsets[t.id] ?? ""}
              onChange={(e) =>
                setOffsets((o) => ({ ...o, [t.id]: e.target.value }))
              }
            />
            <Button
              size="sm"
              variant="ghost"
              disabled={!offsets[t.id]?.trim() || replay.markOffset.isPending}
              onClick={() =>
                replay.markOffset.mutate({
                  turnId: t.id,
                  offset: offsets[t.id],
                })
              }
            >
              打点
            </Button>
          </div>
        ))}
      </div>

      {/* 3. 处理 */}
      <Button
        size="sm"
        disabled={!uploaded || replay.process.isPending}
        onClick={() => replay.process.mutate()}
      >
        {replay.process.isPending ? "已提交…" : "▶ 按打点转写并评分"}
      </Button>
      {replay.upload.error && (
        <p className="text-destructive">上传失败：{(replay.upload.error as Error).message}</p>
      )}
      <p className="text-muted-foreground">
        处理异步进行（几分钟），完成后各题回答与评分自动出现在时间线。
      </p>
    </div>
  );
}

function extractErr(err: unknown): string | null {
  const msg = (
    err as { response?: { data?: { error?: { message?: string } } } }
  )?.response?.data?.error?.message;
  return msg ?? null;
}

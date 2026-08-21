"use client";

/**
 * 连续监听录音器（M2a+ 自动模式）：
 * 双路设备（会议声/物理麦）→ WebAudio 音量 VAD 自动切片 → 静音 2.5s 视为一段话完
 * → 自动上传转写评分 → 面试全程零点击。
 *
 * 说话人分离 = 物理隔离：loopback 设备只含会议对端声音；
 * 物理麦路默认不上传（面试官念题无需留声），仅作可视化音量参考。
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

interface Props {
  onSegment: (blob: Blob, filename: string) => void;
  uploadPending: boolean;
}

/** VAD 参数（秒） */
const SPEECH_MIN = 0.8; // 短于此不算有效说话(防误触发)
const SILENCE_SPLIT = 2.5; // 静音超此值切段上传
const RMS_SPEECH = 0.008; // 音量阈值(loopback 信号较弱,实测调低)

export function ContinuousRecorder({ onSegment, uploadPending }: Props) {
  const [devices, setDevices] = useState<MediaDeviceInfo[]>([]);
  const [deviceId, setDeviceId] = useState<string>("");
  const [running, setRunning] = useState(false);
  const [level, setLevel] = useState(0);
  const [state, setState] = useState<"idle" | "listening" | "speaking" | "uploading">("idle");
  const [segCount, setSegCount] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const ctxRef = useRef<AudioContext | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const speechStartRef = useRef<number | null>(null);
  const lastVoiceRef = useRef<number>(0);
  const rafRef = useRef<number | null>(null);
  const closingRef = useRef(false);

  useEffect(() => {
    navigator.mediaDevices
      ?.enumerateDevices()
      .then((all) =>
        setDevices(all.filter((d) => d.kind === "audioinput" && d.deviceId && d.label)),
      )
      .catch(() => undefined);
  }, []);

  const flushSegment = useCallback(() => {
    const rec = recorderRef.current;
    if (!rec || rec.state === "inactive") return;
    closingRef.current = true;
    rec.stop(); // onstop 里组 blob 上传
  }, []);

  const loop = useCallback(() => {
    const analyser = analyserRef.current;
    if (!analyser) return;
    const buf = new Float32Array(analyser.fftSize);
    const tick = () => {
      if (!analyserRef.current) return;
      analyserRef.current.getFloatTimeDomainData(buf);
      // RMS 音量
      let sum = 0;
      for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
      const rms = Math.sqrt(sum / buf.length);
      setLevel(Math.min(1, rms * 40));
      const now = performance.now();

      if (rms > RMS_SPEECH) {
        lastVoiceRef.current = now;
        if (speechStartRef.current === null) {
          speechStartRef.current = now;
          setState("speaking");
        }
      } else if (speechStartRef.current !== null) {
        const spoke = lastVoiceRef.current - speechStartRef.current;
        const silentFor = now - lastVoiceRef.current;
        if (spoke < SPEECH_MIN * 1000 && silentFor > 1200) {
          // 太短,忽略:重置不切
          speechStartRef.current = null;
          setState("listening");
        } else if (silentFor > SILENCE_SPLIT * 1000) {
          // 说完一段:切段
          speechStartRef.current = null;
          setState("uploading");
          flushSegment();
          return; // 等 onstop 重建 recorder
        }
      }
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
  }, [flushSegment]);

  const startRec = useCallback(() => {
    if (!streamRef.current) return;
    const rec = new MediaRecorder(streamRef.current, {
      mimeType: MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
        ? "audio/webm;codecs=opus"
        : "audio/webm",
    });
    chunksRef.current = [];
    closingRef.current = false;
    rec.ondataavailable = (e) => e.data.size > 0 && chunksRef.current.push(e.data);
    rec.onstop = () => {
      const blob = new Blob(chunksRef.current, { type: "audio/webm" });
      if (blob.size > 1000) {
        setSegCount((c) => c + 1);
        onSegment(blob, `seg-${Date.now()}.webm`);
      }
      // 重建 recorder 继续监听
      if (runningRef.current) {
        setTimeout(() => {
          if (runningRef.current) {
            startRec();
            setState("listening");
          }
        }, 200);
      }
    };
    rec.start(500);
    recorderRef.current = rec;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [onSegment]);

  const runningRef = useRef(false);

  const start = useCallback(async () => {
    setError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: deviceId ? { deviceId: { exact: deviceId } } : true,
      });
      streamRef.current = stream;
      const ctx = new AudioContext();
      ctxRef.current = ctx;
      const src = ctx.createMediaStreamSource(stream);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 2048;
      src.connect(analyser);
      analyserRef.current = analyser;

      runningRef.current = true;
      setRunning(true);
      setSegCount(0);
      setState("listening");
      startRec();
      loop();
    } catch {
      setError("无法访问音频设备：请检查浏览器麦克风权限；录会议声需选 loopback 设备（如 LarkAudioDevice）");
    }
  }, [deviceId, loop, startRec]);

  const stop = useCallback(() => {
    runningRef.current = false;
    setRunning(false);
    setState("idle");
    if (rafRef.current) cancelAnimationFrame(rafRef.current);
    if (recorderRef.current && recorderRef.current.state !== "inactive") {
      recorderRef.current.stop();
    }
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    ctxRef.current?.close().catch(() => undefined);
    ctxRef.current = null;
    analyserRef.current = null;
  }, []);

  useEffect(
    () => () => {
      runningRef.current = false;
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      streamRef.current?.getTracks().forEach((t) => t.stop());
      ctxRef.current?.close().catch(() => undefined);
    },
    [],
  );

  const stateLabel = {
    idle: "未启动",
    listening: "🎧 监听中(等待说话)",
    speaking: "🔴 录音中",
    uploading: "⇪ 切片上传中",
  }[state];

  return (
    <div className="space-y-2 rounded-md border p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium">连续监听模式</span>
        {state !== "idle" && <Badge variant={state === "speaking" ? "destructive" : "secondary"}>{stateLabel}</Badge>}
        {segCount > 0 && <Badge variant="outline">已自动切 {segCount} 段</Badge>}
        {!running ? (
          <Button size="sm" onClick={start} disabled={uploadPending}>
            ▶ 启动监听
          </Button>
        ) : (
          <Button size="sm" variant="outline" onClick={stop}>
            ■ 停止
          </Button>
        )}
      </div>

      <select
        className="w-full rounded border bg-background px-2 py-1 text-xs"
        value={deviceId}
        onChange={(e) => setDeviceId(e.target.value)}
        disabled={running}
      >
        <option value="">🎤 默认麦克风</option>
        {devices.map((d) => (
          <option key={d.deviceId} value={d.deviceId}>
            {d.label || `设备 ${d.deviceId.slice(0, 8)}`}
          </option>
        ))}
      </select>

      {/* 音量条(实时反馈,帮助确认选对设备) */}
      <div className="h-1.5 overflow-hidden rounded bg-muted">
        <div
          className={`h-full transition-all ${state === "speaking" ? "bg-red-500" : "bg-blue-400"}`}
          style={{ width: `${Math.round(level * 100)}%` }}
        />
      </div>

      <p className="text-[11px] leading-relaxed text-muted-foreground">
        提示：选 loopback 设备（如 LarkAudioDevice / BlackHole）录会议声——只含候选人
        声音，自动说话人分离。对方说完一段（静音 2.5 秒）自动上传转写评分，
        全程无需点击。面试官念题不会被录入。
      </p>
      {error && <p className="text-xs text-destructive">{error}</p>}
    </div>
  );
}

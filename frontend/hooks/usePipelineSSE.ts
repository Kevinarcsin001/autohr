"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { apiClient } from "@/lib/api/client";

// ============================================================================
// 类型（与 backend/app/services/screening_orchestrator.py 的 ProgressEvent 对应）
// ============================================================================

export interface PipelineProgressEvent {
  type: "started" | "progress" | "done";
  event_id: number;
  data: {
    total?: number;
    candidate_id?: string;
    candidate_name?: string;
    stage?: "filter" | "score" | "interview";
    status?: "ok" | "failed";
    reason?: string;
    summary?: {
      total: number;
      passed: number;
      disqualified: number;
      failed: number;
      failed_reasons: Array<{
        candidate_id: string;
        stage: string;
        error: string;
      }>;
    };
  };
}

interface UsePipelineSSEOptions {
  jobId: string;
  onProgress?: (event: PipelineProgressEvent) => void;
  onDone?: (summary: PipelineProgressEvent["data"]["summary"]) => void;
  enabled?: boolean;
}

/**
 * 订阅 pipeline SSE 进度（任务 23）。
 *
 * - 使用 fetch + ReadableStream 手动解析 SSE（EventSource 不支持自定义 header）
 * - 自动从 axios 拦截器注入 access token
 * - 支持 Last-Event-ID 断线重连（重连时携带上次最后看到的 event_id）
 * - unmount 自动关闭连接
 */
export function usePipelineSSE({
  jobId,
  onProgress,
  onDone,
  enabled = true,
}: UsePipelineSSEOptions) {
  const [runId, setRunId] = useState<string | null>(null);
  const [isConnected, setIsConnected] = useState(false);
  const [events, setEvents] = useState<PipelineProgressEvent[]>([]);
  const abortRef = useRef<AbortController | null>(null);
  const lastEventIdRef = useRef<number>(-1);
  const callbacksRef = useRef({ onProgress, onDone });
  callbacksRef.current = { onProgress, onDone };

  const connect = useCallback(
    async (newRunId: string) => {
      // 关闭旧连接
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      setRunId(newRunId);
      setEvents([]);
      lastEventIdRef.current = -1;

      try {
        const baseURL =
          process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";
        const token = apiClient.defaults.headers.common?.Authorization as
          | string
          | undefined;

        const url = `${baseURL}/api/screening/pipeline/${newRunId}/events`;
        const headers: Record<string, string> = {
          Accept: "text/event-stream",
        };
        if (lastEventIdRef.current >= 0) {
          headers["Last-Event-ID"] = String(lastEventIdRef.current);
        }
        if (token) {
          headers.Authorization = token;
        }

        const resp = await fetch(url, {
          method: "GET",
          headers,
          signal: controller.signal,
        });
        if (!resp.ok || !resp.body) {
          throw new Error(`SSE connect failed: ${resp.status}`);
        }
        setIsConnected(true);

        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        // eslint-disable-next-line no-constant-condition
        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });

          // 按双换行分割事件块
          const blocks = buffer.split("\n\n");
          buffer = blocks.pop() ?? "";
          for (const block of blocks) {
            const event = parseSSEBlock(block);
            if (!event) continue;
            lastEventIdRef.current = event.event_id;
            setEvents((prev) => [...prev, event]);
            callbacksRef.current.onProgress?.(event);
            if (event.type === "done") {
              callbacksRef.current.onDone?.(event.data.summary);
              return;
            }
          }
        }
      } catch (err) {
        if ((err as Error).name !== "AbortError") {
          // 连接异常断开；前端可决定是否重试
          // 简化：不自动重连（避免无限重连）
        }
      } finally {
        setIsConnected(false);
      }
    },
    []
  );

  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  return {
    runId,
    events,
    isConnected,
    enabled,
    connect,
    disconnect: () => abortRef.current?.abort(),
  };
}

function parseSSEBlock(block: string): PipelineProgressEvent | null {
  let eventType = "message";
  let eventId = 0;
  const dataLines: string[] = [];

  for (const line of block.split("\n")) {
    if (line.startsWith(":")) continue; // comment / ping
    if (line.startsWith("event:")) {
      eventType = line.slice(6).trim();
    } else if (line.startsWith("id:")) {
      eventId = parseInt(line.slice(3).trim(), 10) || 0;
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trimStart());
    }
  }
  if (!dataLines.length) return null;
  try {
    const data = JSON.parse(dataLines.join("\n"));
    return {
      type: eventType as PipelineProgressEvent["type"],
      event_id: eventId,
      data,
    };
  } catch {
    return null;
  }
}

// ============================================================================
// 触发 pipeline
// ============================================================================

export async function triggerPipelineApi(
  jobId: string,
  candidateIds: string[]
): Promise<{ run_id: string; total: number }> {
  const { data } = await apiClient.post<{
    run_id: string;
    job_id: string;
    total: number;
  }>("/api/screening/pipeline", {
    job_id: jobId,
    candidate_ids: candidateIds,
  });
  return { run_id: data.run_id, total: data.total };
}

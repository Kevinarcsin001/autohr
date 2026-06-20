"use client";

import { useCallback, useRef, useState } from "react";

import {
  confirmUploads,
  createUploadIntent,
  putFileToSignedUrl,
  type UploadConfirmItemInput,
} from "@/lib/api/uploads";

// ============================================================================
// 单文件状态机
// ============================================================================

export type UploadFileStatus =
  | "queued"
  | "uploading"
  | "confirming"
  | "done"
  | "rejected"
  | "failed";

export interface UploadFileEntry {
  /** 客户端临时 ID（同一文件重试保持稳定，便于 React key） */
  localId: string;
  file: File;
  status: UploadFileStatus;
  /** 0-100 上传进度（仅 uploading 阶段有意义） */
  progress: number;
  /** 失败/拒绝原因 */
  reason: string | null;
  /** 服务端 resume_id（confirm 后填入） */
  resumeId: string | null;
  /** 内部：intent 拿到的 file_key + signed_url + upload_id */
  _fileKey?: string;
  _signedUrl?: string;
  _uploadId?: string;
}

interface UploadOptions {
  /** 并发上传上限（默认 4） */
  concurrency?: number;
  /** 单文件大小上限（字节，默认 20 MB） */
  maxFileBytes?: number;
  /** 允许的扩展名集合（小写，无点） */
  allowedExtensions?: Set<string>;
  /** 关联的职位 ID */
  jobId?: string | null;
  /** 单文件上传完成后的回调（用于刷新 candidates 列表等） */
  onComplete?: () => void;
}

const DEFAULT_ALLOWED_EXT = new Set([
  "pdf",
  "doc",
  "docx",
  "png",
  "jpg",
  "jpeg",
]);

const DEFAULT_MAX_FILE_BYTES = 20 * 1024 * 1024;
const DEFAULT_CONCURRENCY = 4;

const REJECT_LABEL: Record<string, string> = {
  size_exceeded: "超过单文件大小上限",
  extension_not_allowed: "扩展名不在允许列表",
  batch_too_large: "批次文件数过多",
  object_missing: "对象未找到（直传可能失败）",
  mime_not_allowed: "MIME 类型不允许",
  mime_mismatch: "扩展名与实际内容不一致",
  cross_team: "跨团队访问被拒",
  duplicate_enqueue: "该文件已上传过",
  local_oversize: "超过单文件大小上限（客户端）",
  local_extension: "扩展名不允许（客户端）",
  local_put_failed: "上传失败",
  local_unknown: "未知错误",
};

export function reasonToLabel(reason: string | null): string {
  if (!reason) return "";
  return REJECT_LABEL[reason] ?? reason;
}

function fileExt(name: string): string {
  const i = name.lastIndexOf(".");
  if (i < 0) return "";
  return name.slice(i + 1).toLowerCase();
}

/**
 * 上传中心 hook。
 *
 * 流程：
 * 1. addFiles(files): 客户端预筛（大小/扩展名）+ 加入队列
 * 2. startAll(): 并发上传（默认 4）
 *    - 每个文件：intent → PUT 直传 → confirm
 *    - 单文件失败不阻塞其他
 * 3. retry(localId): 重新走整个流程
 * 4. remove(localId): 从列表移除
 */
export function useUploads(options: UploadOptions = {}) {
  const concurrency = options.concurrency ?? DEFAULT_CONCURRENCY;
  const maxFileBytes = options.maxFileBytes ?? DEFAULT_MAX_FILE_BYTES;
  const allowedExt = options.allowedExtensions ?? DEFAULT_ALLOWED_EXT;

  const [entries, setEntries] = useState<UploadFileEntry[]>([]);
  const seqRef = useRef(0);

  const patchEntry = useCallback(
    (localId: string, patch: Partial<UploadFileEntry>) => {
      setEntries((prev) =>
        prev.map((e) => (e.localId === localId ? { ...e, ...patch } : e))
      );
    },
    []
  );

  const addFiles = useCallback(
    (files: FileList | File[]) => {
      const arr = Array.from(files);
      const newEntries: UploadFileEntry[] = arr.map((file) => {
        seqRef.current += 1;
        const localId = `local-${Date.now()}-${seqRef.current}`;
        const ext = fileExt(file.name);
        let status: UploadFileStatus = "queued";
        let reason: string | null = null;
        if (file.size === 0 || file.size > maxFileBytes) {
          status = "rejected";
          reason = "local_oversize";
        } else if (!allowedExt.has(ext)) {
          status = "rejected";
          reason = "local_extension";
        }
        return {
          localId,
          file,
          status,
          progress: 0,
          reason,
          resumeId: null,
        };
      });
      setEntries((prev) => [...prev, ...newEntries]);
    },
    [allowedExt, maxFileBytes]
  );

  const remove = useCallback((localId: string) => {
    setEntries((prev) => prev.filter((e) => e.localId !== localId));
  }, []);

  const clearCompleted = useCallback(() => {
    setEntries((prev) =>
      prev.filter(
        (e) => e.status !== "done" && e.status !== "rejected"
      )
    );
  }, []);

  const uploadOne = useCallback(
    async (entry: UploadFileEntry): Promise<void> => {
      const file = entry.file;
      // intent
      let intent;
      try {
        intent = await createUploadIntent([
          {
            filename: file.name,
            size_bytes: file.size,
            mime_client: file.type || "application/octet-stream",
          },
        ]);
      } catch (err) {
        patchEntry(entry.localId, {
          status: "failed",
          reason: "local_unknown",
        });
        return;
      }
      const intentItem = intent.items[0];
      if (intentItem.status === "rejected" || !intentItem.signed_url) {
        patchEntry(entry.localId, {
          status: "rejected",
          reason: intentItem.reject_reason ?? "local_unknown",
        });
        return;
      }
      patchEntry(entry.localId, {
        _fileKey: intentItem.file_key,
        _signedUrl: intentItem.signed_url,
        _uploadId: intentItem.upload_id,
        status: "uploading",
      });

      // PUT 直传
      try {
        await putFileToSignedUrl(
          intentItem.signed_url,
          file,
          file.type || "application/octet-stream",
          (loaded, total) => {
            patchEntry(entry.localId, {
              progress: Math.round((loaded / total) * 100),
            });
          }
        );
      } catch {
        patchEntry(entry.localId, {
          status: "failed",
          reason: "local_put_failed",
        });
        return;
      }

      // confirm
      patchEntry(entry.localId, { status: "confirming" });
      try {
        const confirmInput: UploadConfirmItemInput = {
          upload_id: intentItem.upload_id,
          file_key: intentItem.file_key,
        };
        const result = await confirmUploads([confirmInput], options.jobId);
        const r = result.items[0];
        if (r.status === "ok") {
          patchEntry(entry.localId, {
            status: "done",
            resumeId: r.resume_id,
            progress: 100,
          });
          options.onComplete?.();
        } else {
          patchEntry(entry.localId, {
            status: "rejected",
            reason: r.reject_reason ?? "local_unknown",
          });
        }
      } catch {
        patchEntry(entry.localId, {
          status: "failed",
          reason: "local_unknown",
        });
      }
    },
    [patchEntry, options]
  );

  const startAll = useCallback(async () => {
    // 取所有 queued / failed 状态的（failed 允许重试）
    const pending = entries.filter(
      (e) => e.status === "queued" || e.status === "failed"
    );
    if (pending.length === 0) return;

    // 简易并发池：最多 N 个 in-flight
    let cursor = 0;
    const workers: Promise<void>[] = [];
    const next = async (): Promise<void> => {
      while (cursor < pending.length) {
        const idx = cursor++;
        // 拿最新的 entry（避免闭包陈旧 state）
        const current = await new Promise<UploadFileEntry>((resolve) => {
          setEntries((prev) => {
            const found = prev.find((e) => e.localId === pending[idx].localId);
            resolve(found ?? pending[idx]);
            return prev;
          });
        });
        await uploadOne(current);
      }
    };
    for (let i = 0; i < Math.min(concurrency, pending.length); i++) {
      workers.push(next());
    }
    await Promise.all(workers);
  }, [concurrency, entries, uploadOne]);

  const retry = useCallback(
    async (localId: string) => {
      const entry = entries.find((e) => e.localId === localId);
      if (!entry) return;
      patchEntry(localId, {
        status: "queued",
        progress: 0,
        reason: null,
        resumeId: null,
      });
      // 立即跑这一个
      await uploadOne({ ...entry, status: "queued", progress: 0, reason: null });
    },
    [entries, patchEntry, uploadOne]
  );

  return {
    entries,
    addFiles,
    remove,
    clearCompleted,
    startAll,
    retry,
  };
}

import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

/**
 * Tailwind 类名合并工具：clsx + tailwind-merge。
 * 处理冲突的 Tailwind 类（如 px-2 和 px-4 取后者）。
 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/**
 * 格式化日期为 YYYY-MM-DD HH:mm。
 */
export function formatDateTime(date: Date | string | number): string {
  const d = new Date(date);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/**
 * 格式化文件大小。
 */
export function formatFileSize(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(2))} ${sizes[i]}`;
}

/**
 * 在原文中查找 needle 子串位置，返回带前后 30 字符上下文的片段。
 *
 * 任务 24 用：推荐理由点击"查看依据"时，把 parsed_text 中的对应片段高亮。
 *
 * 行为：
 * - 大小写不敏感匹配（中文也按字符处理）
 * - 未命中 → 返回 null（调用方决定降级文案）
 * - 命中 → 返回 { start, end, prefix, match, suffix }
 *   - prefix / suffix 各取 15 字符上下文（避免渲染整篇简历）
 *
 * @param text 全文（如简历 parsed_text）
 * @param needle 关键词（如理由的前 12-16 字符）
 */
export interface HighlightMatch {
  start: number;
  end: number;
  prefix: string;
  match: string;
  suffix: string;
}

const HIGHLIGHT_CONTEXT_CHARS = 15;

export function highlightSubstring(
  text: string | null | undefined,
  needle: string | null | undefined,
): HighlightMatch | null {
  if (!text || !needle) return null;
  const textStr = String(text);
  const needleStr = String(needle).trim();
  if (!needleStr) return null;

  const idx = textStr.toLowerCase().indexOf(needleStr.toLowerCase());
  if (idx < 0) return null;

  const end = idx + needleStr.length;
  const prefixStart = Math.max(0, idx - HIGHLIGHT_CONTEXT_CHARS);
  const suffixEnd = Math.min(
    textStr.length,
    end + HIGHLIGHT_CONTEXT_CHARS,
  );

  return {
    start: idx,
    end,
    prefix: textStr.slice(prefixStart, idx),
    match: textStr.slice(idx, end),
    suffix: textStr.slice(end, suffixEnd),
  };
}

/**
 * 从推荐理由文本中提取"前 N 个字符"作为高亮匹配关键词。
 *
 * 理由通常是完整短句（如"5 年 Python 后端开发经验，熟悉 FastAPI 框架"）；
 * 取前 12-16 字符作为索引关键词以提高匹配命中率。
 */
export function extractNeedleFromReason(
  reason: string | null | undefined,
  maxLen = 14,
): string {
  if (!reason) return "";
  const trimmed = reason.trim();
  // 优先取逗号 / 句号 / 顿号前的部分
  const firstClause = trimmed.split(/[，,。、；;:]/)[0] ?? trimmed;
  if (firstClause.length <= maxLen) return firstClause;
  return firstClause.slice(0, maxLen);
}

"use client";

import type { CandidateListParams } from "@/lib/api/candidates";
import type { FilterFormState } from "@/components/CandidateFilters";

/**
 * 任务 23：保存视图（localStorage）。
 *
 * 用户当前筛选条件 + 列配置 + 密度可保存为命名视图，便于下次快速切换。
 * 视图以 jobId 维度隔离（不同职位通常有不同筛选习惯）。
 */

// ============================================================================
// 类型
// ============================================================================

export type Density = "compact" | "default" | "comfortable";

export interface ColumnConfig {
  /** 列 id（与 TanStack Table column.id 对应） */
  id: string;
  /** 是否显示 */
  visible: boolean;
  /** 显示顺序（从 0 开始；缺失的列追加到末尾） */
  order: number;
}

export interface SavedView {
  id: string;
  name: string;
  /** 创建时间（ISO 字符串） */
  created_at: string;
  /** 筛选条件（与 URL query 同构） */
  filters: FilterFormState;
  /** 分组 */
  group: string;
  /** 列配置 */
  columns: ColumnConfig[];
  /** 表格密度 */
  density: Density;
  /** 页大小 */
  page_size: number;
}

export interface SavedViewStored {
  views: SavedView[];
}

// ============================================================================
// 存储 key
// ============================================================================

const STORAGE_PREFIX = "autohr:views";

function storageKey(jobId: string): string {
  return `${STORAGE_PREFIX}:${jobId}`;
}

// ============================================================================
// 读写 API
// ============================================================================

/**
 * 读取某 job 的所有保存视图（按创建时间倒序）。
 *
 * 容错：localStorage 不可用（SSR / 隐私模式）→ 返回空数组。
 */
export function listSavedViews(jobId: string): SavedView[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(storageKey(jobId));
    if (!raw) return [];
    const parsed = JSON.parse(raw) as SavedViewStored;
    if (!Array.isArray(parsed.views)) return [];
    return [...parsed.views].sort(
      (a, b) =>
        new Date(b.created_at).getTime() -
        new Date(a.created_at).getTime(),
    );
  } catch {
    return [];
  }
}

/**
 * 写入或更新（id 已存在则覆盖）一个视图。
 */
export function upsertSavedView(jobId: string, view: SavedView): void {
  if (typeof window === "undefined") return;
  const views = listSavedViews(jobId);
  const idx = views.findIndex((v) => v.id === view.id);
  if (idx >= 0) {
    views[idx] = view;
  } else {
    views.push(view);
  }
  persist(jobId, views);
}

/**
 * 删除一个视图。
 */
export function deleteSavedView(jobId: string, viewId: string): void {
  if (typeof window === "undefined") return;
  const views = listSavedViews(jobId).filter((v) => v.id !== viewId);
  persist(jobId, views);
}

/**
 * 生成短 ID（避免依赖 uuid 包）。
 */
export function generateViewId(): string {
  const ts = Date.now().toString(36);
  const rand = Math.random().toString(36).slice(2, 8);
  return `v_${ts}_${rand}`;
}

/**
 * 将当前筛选 + 列配置快照为 SavedView。
 *
 * 深拷贝 filters/columns：保存后调用方对原对象的修改不应污染快照。
 */
export function snapshotView(
  name: string,
  data: {
    filters: FilterFormState;
    group: string;
    columns: ColumnConfig[];
    density: Density;
    page_size: number;
  },
): SavedView {
  return {
    id: generateViewId(),
    name,
    created_at: new Date().toISOString(),
    // structuredClone 比 JSON.parse(JSON.stringify(x)) 更准确（保留 Date 等）
    // 这里 filters/columns 都是纯对象，structuredClone 也胜任
    filters: structuredClone(data.filters),
    group: data.group,
    columns: data.columns.map((c) => ({ ...c })),
    density: data.density,
    page_size: data.page_size,
  };
}

/**
 * 校验 SavedView 是否仍兼容当前代码（防止 schema 演进破坏老数据）。
 */
export function isViewCompatible(view: unknown): view is SavedView {
  if (typeof view !== "object" || view === null) return false;
  const v = view as Record<string, unknown>;
  return (
    typeof v.id === "string" &&
    typeof v.name === "string" &&
    typeof v.created_at === "string" &&
    typeof v.group === "string" &&
    Array.isArray(v.columns) &&
    typeof v.density === "string" &&
    typeof v.page_size === "number"
  );
}

// ============================================================================
// 内部
// ============================================================================

function persist(jobId: string, views: SavedView[]): void {
  try {
    const payload: SavedViewStored = { views };
    window.localStorage.setItem(storageKey(jobId), JSON.stringify(payload));
  } catch {
    // 配额超限 / 隐私模式：忽略（视图保存是 nice-to-have）
  }
}

"use client";

import type { CandidateListParams } from "@/lib/api/candidates";

/**
 * 任务 23：候选人列表 URL 查询参数 <-> 业务对象 的双向转换。
 *
 * 抽离为独立模块（SRP）：
 * - page.tsx 仅做粘合（state + JSX）
 * - 此模块负责 URL <-> 业务参数的纯函数转换
 * - 易于单元测试（无 React 依赖）
 *
 * URL 字段约定（与 backend/app/api/job_candidates.py 的 query params 一一对应）：
 *   group, page, page_size, sort_by, sort_order,
 *   skill, education, source,
 *   min_score, max_score, min_years, max_years
 */

// ============================================================================
// 类型
// ============================================================================

export type CandidateGroupLiteral =
  | "all"
  | "passed"
  | "disqualified"
  | "pending";

export interface CandidatesUrlState {
  group: CandidateGroupLiteral;
  page: number;
  page_size: number;
  sort_by: string;
  sort_order: "asc" | "desc";
  skill: string;
  education: string;
  source: string;
  min_score: number | undefined;
  max_score: number | undefined;
  min_years: number | undefined;
  max_years: number | undefined;
}

// ============================================================================
// 默认值
// ============================================================================

export const DEFAULT_GROUP: CandidateGroupLiteral = "all";
export const DEFAULT_PAGE = 1;
export const DEFAULT_PAGE_SIZE = 50;
export const DEFAULT_SORT_BY = "total";
export const DEFAULT_SORT_ORDER: "asc" | "desc" = "desc";

export const ALLOWED_PAGE_SIZES = [10, 20, 50, 100] as const;
export const ALLOWED_GROUPS: CandidateGroupLiteral[] = [
  "all",
  "passed",
  "disqualified",
  "pending",
];

// ============================================================================
// URL → 业务对象
// ============================================================================

/**
 * 解析 URLSearchParams 为 CandidatesUrlState。
 *
 * 安全性：
 * - 所有字段防御性 clamp（page ≥ 1；page_size ∈ ALLOWED_PAGE_SIZES）
 * - 非法值回退默认值，不抛错
 */
export function parseCandidatesUrl(sp: URLSearchParams): CandidatesUrlState {
  const groupRaw = sp.get("group");
  const group = (
    groupRaw && ALLOWED_GROUPS.includes(groupRaw as CandidateGroupLiteral)
      ? groupRaw
      : DEFAULT_GROUP
  ) as CandidateGroupLiteral;

  const pageRaw = Number(sp.get("page") ?? DEFAULT_PAGE);
  const page = Number.isFinite(pageRaw) ? Math.max(1, Math.floor(pageRaw)) : DEFAULT_PAGE;

  const pageSizeRaw = Number(sp.get("page_size") ?? DEFAULT_PAGE_SIZE);
  const page_size = (ALLOWED_PAGE_SIZES as readonly number[]).includes(
    pageSizeRaw,
  )
    ? pageSizeRaw
    : DEFAULT_PAGE_SIZE;

  const sort_by = sp.get("sort_by") || DEFAULT_SORT_BY;
  const sort_order: "asc" | "desc" =
    sp.get("sort_order") === "asc" ? "asc" : DEFAULT_SORT_ORDER;

  return {
    group,
    page,
    page_size,
    sort_by,
    sort_order,
    skill: sp.get("skill") ?? "",
    education: sp.get("education") ?? "",
    source: sp.get("source") ?? "",
    min_score: parseOptionalNumber(sp.get("min_score")),
    max_score: parseOptionalNumber(sp.get("max_score")),
    min_years: parseOptionalNumber(sp.get("min_years")),
    max_years: parseOptionalNumber(sp.get("max_years")),
  };
}

/**
 * 将 CandidatesUrlState 转换为 CandidateListParams（供 API 调用）。
 *
 * 转换：
 * - 空字符串过滤为 undefined（axios 不会发送 undefined 字段）
 * - 类型断言：URL 中是 string，业务上是 union type；这里信任后端校验
 */
export function urlStateToCandidateParams(
  state: CandidatesUrlState,
): CandidateListParams {
  return {
    group: state.group,
    sort_by: state.sort_by as CandidateListParams["sort_by"],
    sort_order: state.sort_order,
    page: state.page,
    page_size: state.page_size,
    skill: state.skill || undefined,
    education: (state.education || undefined) as CandidateListParams["education"],
    source: (state.source || undefined) as CandidateListParams["source"],
    min_score: state.min_score,
    max_score: state.max_score,
    min_years: state.min_years,
    max_years: state.max_years,
  };
}

/**
 * 业务对象 → URLSearchParams（用于 router.replace）。
 *
 * - 空值/null/undefined → 删除该 key
 * - 默认值也写入（显式优于隐式；便于分享）
 */
export function candidatesUrlStateToSearchParams(
  state: Partial<CandidatesUrlState>,
): URLSearchParams {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(state)) {
    if (v === null || v === undefined || v === "") continue;
    sp.set(k, String(v));
  }
  return sp;
}

// ============================================================================
// 内部
// ============================================================================

function parseOptionalNumber(v: string | null): number | undefined {
  if (v === null || v === "") return undefined;
  const n = Number(v);
  if (Number.isNaN(n)) return undefined;
  return n;
}
